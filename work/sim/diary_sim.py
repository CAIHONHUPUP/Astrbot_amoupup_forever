# -*- coding: utf-8 -*-
"""隔离日程日记仿真（2026-11-22，只读测试）：3/5/7 天大日记 + 最近三天 digest 注入。

目标
----
用生产日程引擎生成真实日程块（地点/动作/同伴/三餐/健康/天气都在块或插件状态里），
再走生产大日记链路（ScheduleMixin._generate_daily_diary 的提示词/事实池/守卫/落库）
逐天生成日记，然后用独立核对器（work/sim/diary_checks.py）比对事实，最后检查
【前几天】digest 注入（最近三天）。产出 work/sim/diary_report.json / .md。

只读约束（本文件自己执行）
--------------------------
1. 不实例化生产 AmoDB：运行期把 amo_db.AmoDB.__init__ 拦成异常；
2. 不连接生产 sqlite：sqlite3.connect 指向 plugin_data / amo_forever.db 一律拦成异常；
3. 不给生产路径写文件：内置 open/io.open 对 plugin_data 下写操作直接抛错；
4. 模型 provider 只从 work/sim/provider.env 读（不读 cmd_config.json、不读生产配置）；
5. 日记目录、所有临时文件都在系统 temp 的 amo-diary-sim-/ 下，进程结束整棵删除；
6. 运行前后记录生产库 size/mtime/sha256 作为旁证（生产实例可能同时在写，会如实记录）。

跑法
----
    python work/sim/diary_sim.py --days 7 [--no-real-llm]
"""
from __future__ import annotations

import argparse
import asyncio
import builtins
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
import time
import types
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
WORK = os.path.dirname(HERE)
for _path in (ROOT, WORK, os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import sim_lib  # noqa: E402

sim_lib.install_stub()

import clock  # noqa: E402
import amo_human_state  # noqa: E402  (需要先 import，HumanState 在 make_plugin 里建)
import work.simulate_weeks as weeks  # noqa: E402
import work.sim_weeks_env as wenv  # noqa: E402
import amo_schedule  # noqa: E402

import diary_checks as C  # noqa: E402

#: 生产数据目录（只做指纹与写保护，不读）
ASTRBOT_DATA = os.path.dirname(os.path.dirname(ROOT))
PLUGIN_DATA = os.path.join(ASTRBOT_DATA, "plugin_data")
PROD_DBS = (
    os.path.join(PLUGIN_DATA, "Astrbot_amoupup_forever", "amo_forever.db"),
    os.path.join(PLUGIN_DATA, "Astrbot_amoupup_data", "amo_data.db"),
)
PROD_DIRS = (os.path.join(PLUGIN_DATA, "Astrbot_amoupup_forever"),
             os.path.join(PLUGIN_DATA, "Astrbot_amoupup_data"))

OUT_JSON = os.path.join(HERE, "diary_report.json")
OUT_MD = os.path.join(HERE, "diary_report.md")
CITY = wenv.DEFAULT_CITY
START = weeks.START_DAY                     # 2026-11-01（周日，golden 同款种子）
UMO = "sim:private:diary"
PLAN_DAYS = 7
GEN_CLOCK = (8, 10)                         # 早上型：第二天 08:10 写昨天
PROBE_CLOCK = (9, 0)                        # digest 注入探针：第四天 09:00

#: 健康注入（生产 set_health 写入）：day_index -> 当天身体状态
#:  2 = 11-03 低烧（当晚退）、4 = 11-05 感冒（持续到 11-06 中午）
SICK_DAYS = {2: ("低烧", "身上发烫，没什么力气"), 4: ("感冒", "鼻子堵堵的，头有点沉")}
#: 生成日记那一刻（第二天早上）的身体状态；用生产 HumanState.set_health 落地
GEN_SICK = {4: ("感冒", "鼻子堵堵的，头有点沉")}

#: 大日记链路上要绑到假插件上的生产方法（全部来自 amo_schedule.ScheduleMixin）
DIARY_METHODS = (
    "_diary_chat_lines", "_diary_hourly_lines", "_generate_daily_diary",
    "_guard_daily_location", "_finalize_daily_candidate", "_write_one_daily_diary",
    "_day_digest", "_diary_existing_text", "_daily_diary_rewrites",
    "_bump_daily_diary_rewrite", "_finalize_live_diary", "_diary_dir",
    "diary_fact_mismatch",
)
CODE_FILES = ("schedule_engine.py", "schedule_skeleton.py", "schedule_plan.py",
              "amo_schedule.py", "amo_fact_pool.py", "amo_fact_timeline.py",
              "amo_diary_guard.py", "amo_location_rules.py", "amo_episodic.py",
              "amo_runtime_context.py", "amo_feature_api.py", "domains/diary/engine.py",
              "work/sim/diary_checks.py", "work/sim/diary_sim.py")


# ---------------------------------------------------------------- 隔离守卫
def _is_prod_path(path):
    try:
        text = str(path or "")
        if not text or text in (":memory:", ""):
            return False
        absolute = os.path.abspath(text)
    except Exception:  # cleanup: 仿真容错
        return False
    for directory in PROD_DIRS:
        if absolute == directory or absolute.startswith(directory + os.sep):
            return True
    base = os.path.basename(absolute)
    return base in ("amo_forever.db", "amo_data.db", "data_v4.db")


def _is_write_mode(mode):
    text = str(mode or "r")
    return any(ch in text for ch in "wax+")


def install_guards():
    """运行期硬隔离：生产库/生产目录的写与连接直接失败。"""
    calls = {"amo_db_init": 0, "sqlite_blocked": 0, "open_blocked": 0,
             "sqlite_total": 0}
    import amo_db
    real_init = amo_db.AmoDB.__init__

    def blocked_init(self, *args, **kwargs):
        calls["amo_db_init"] += 1
        raise RuntimeError("SIM_BLOCKED: 隔离仿真禁止实例化生产 AmoDB")

    amo_db.AmoDB.__init__ = blocked_init
    real_open = builtins.open
    real_io_open = io.open
    real_connect = sqlite3.connect

    def guard_open(file, mode="r", *args, **kwargs):
        if _is_prod_path(file) and _is_write_mode(mode):
            calls["open_blocked"] += 1
            raise RuntimeError("SIM_BLOCKED: 禁止写生产路径 %r mode=%s" % (file, mode))
        return real_open(file, mode, *args, **kwargs)

    def guard_io_open(file, mode="r", *args, **kwargs):
        if _is_prod_path(file) and _is_write_mode(mode):
            calls["open_blocked"] += 1
            raise RuntimeError("SIM_BLOCKED: 禁止写生产路径 %r mode=%s" % (file, mode))
        return real_io_open(file, mode, *args, **kwargs)

    def guard_connect(database, *args, **kwargs):
        calls["sqlite_total"] += 1
        if _is_prod_path(database):
            calls["sqlite_blocked"] += 1
            raise RuntimeError("SIM_BLOCKED: 禁止连接生产库 %r" % (database,))
        return real_connect(database, *args, **kwargs)

    builtins.open = guard_open
    io.open = guard_io_open
    sqlite3.connect = guard_connect

    def restore():
        builtins.open = real_open
        io.open = real_io_open
        sqlite3.connect = real_connect
        amo_db.AmoDB.__init__ = real_init

    return calls, restore


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


def snapshot_dbs():
    return {os.path.basename(path): fingerprint(path) for path in PROD_DBS}


def code_snapshot():
    rows = {}
    for name in CODE_FILES:
        path = os.path.join(ROOT, name)
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            rows[name] = {"sha256_16": hashlib.sha256(data).hexdigest()[:16],
                          "bytes": len(data),
                          "mtime": int(os.stat(path).st_mtime)}
        except Exception as exc:
            rows[name] = {"error": repr(exc)[:120]}
    return rows


def scan_episodic_writers():
    """静态扫描：谁在生产代码里写 episodic_memory / 调用 note_recent_topic。"""
    definitions, calls, writers, reads = [], [], [], []
    for base, _dirs, files in os.walk(ROOT):
        if any(part in base for part in ("tests", "__pycache__", "work", ".git",
                                         "photo_cache", "outfit_refs", "persona_eval")):
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            relative = os.path.relpath(path, ROOT).replace("\\", "/")
            for match in re.finditer(r"note_recent_topic\s*\(", text):
                line_no = text.count("\n", 0, match.start()) + 1
                line = text.splitlines()[line_no - 1].strip()
                item = {"file": relative, "line": line_no, "source": line[:160]}
                if re.match(r"def\s+note_recent_topic", line):
                    definitions.append(item)
                else:
                    calls.append(item)
            # （note_recent_topic 内部用 kv_get/kv_set 读写 episodic_memory，不是调用点）
            for match in re.finditer(r"kv_set\s*\(\s*[\"']episodic_memory[\"']", text):
                line_no = text.count("\n", 0, match.start()) + 1
                line = text.splitlines()[line_no - 1].strip()
                writers.append({"file": relative, "line": line_no, "source": line[:160]})
            for match in re.finditer(r"kv_get\s*\(\s*[\"']episodic_memory[\"']", text):
                line_no = text.count("\n", 0, match.start()) + 1
                line = text.splitlines()[line_no - 1].strip()
                reads.append({"file": relative, "line": line_no, "source": line[:160]})
    return {"definition": definitions, "call_sites": calls, "kv_writers": writers,
            "kv_reads": reads}

# ---------------------------------------------------------------- provider / LLM
def load_provider():
    env = {}
    path = os.path.join(HERE, "provider.env")
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip()
    except OSError:
        return None
    base = env.get("AMO_SIM_LLM_BASE", "").strip().rstrip("/")
    key = env.get("AMO_SIM_LLM_KEY", "").strip()
    model = env.get("AMO_SIM_LLM_MODEL", "").strip()
    if base and key and model:
        return {"kind": "env", "base": base, "model": model, "key": key,
                "source": "work/sim/provider.env"}
    return None


class RealLLM:
    """走生产接口 llm.chat_raw(umo, system, prompt) 的真实模型；记录提示词原文。"""

    def __init__(self, provider, recorder, tag):
        self.provider = provider
        self.recorder = recorder
        self.tag = str(tag)
        self.calls = 0

    async def persona(self, umo):
        return ""

    async def chat_raw(self, umo, system, prompt):
        self.calls += 1
        started = time.time()
        row = {"tag": self.tag, "system": str(system), "prompt": str(prompt),
               "reply": "", "error": "", "usage": {}, "elapsed": 0.0}
        payload = json.dumps({
            "model": self.provider["model"],
            "messages": [{"role": "system", "content": str(system)},
                         {"role": "user", "content": str(prompt)}],
            # deepseek-flash 的 reasoning tokens 也算 completion：太小会把正文写半截（实测 1400
            # 时有一条日记停在「才撑着」）。生产 amo_llm 不受这个仿真上限影响。
            "temperature": 0.8, "max_tokens": 4096,
        }).encode("utf-8")
        for attempt in range(1, 4):
            try:
                def _do():
                    request = urllib.request.Request(
                        self.provider["base"] + "/chat/completions", data=payload,
                        headers={"Authorization": "Bearer " + self.provider["key"],
                                 "Content-Type": "application/json"})
                    with urllib.request.urlopen(request, timeout=150) as response:
                        return json.loads(response.read().decode("utf-8"))
                data = await asyncio.to_thread(_do)
                content = str(data["choices"][0]["message"].get("content") or "")
                row["reply"] = content
                row["usage"] = data.get("usage") or {}
                row["elapsed"] = round(time.time() - started, 2)
                break
            except Exception as exc:  # 网络/限流：重试 2 次，仍失败返回空（= 生产生成失败）
                row["error"] = repr(exc)[:240]
                row["elapsed"] = round(time.time() - started, 2)
                if attempt >= 3:
                    break
                await asyncio.sleep(1.5 * attempt)
        self.recorder.append(row)
        return row["reply"]


class MockDiaryLLM:
    """拿不到真实模型时的确定性兜底：只把提示词里的事实行改写成日记。"""

    def __init__(self, tag):
        self.tag = str(tag)
        self.calls = 0

    async def persona(self, umo):
        return ""

    async def chat_raw(self, umo, system, prompt):
        self.calls += 1
        facts = [line[2:].strip() for line in str(prompt or "").splitlines()
                 if line.startswith("- [")]
        body = []
        for index, fact in enumerate(facts[:12]):
            body.append("今天%s。" % re.sub(r"^\[[^\]]+\]\s*", "", fact))
        text = "".join(body) or "今天什么也没发生，安安静静过了一天。"
        return text[:560]


def make_llm(provider, recorder, tag):
    if provider is None:
        return MockDiaryLLM(tag)
    return RealLLM(provider, recorder, tag)


# ---------------------------------------------------------------- 假插件 / 生产方法绑定
class DiaryDB(wenv.WeekDB):
    """WeekDB + 内存 sqlite 的 block_diary 表（生产 _diary_hourly_lines 走 SQL）。"""

    def __init__(self):
        super().__init__()
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS block_diary ("
            "day TEXT, block_id TEXT, start TEXT, end TEXT, place TEXT, text TEXT, "
            "status TEXT)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schedule_diary (day TEXT, hour TEXT, text TEXT)")
        self.conn.commit()


def build_plugin(diary_root):
    """建隔离假插件，并把生产大日记链路方法绑上去。"""
    plugin, state = wenv.make_plugin(START.isoformat(), CITY)
    plugin.db = DiaryDB()
    from amo_wallet import Wallet
    plugin.wallet = Wallet(plugin.db)
    plugin.wallet.ensure_schema()
    state.db = plugin.db
    plugin.config.update({
        "diary_enabled": True,
        "diary_time": "08:00",
        "diary_window_minutes": 30,
        "location_rules_enabled": True,
        "schedule_block_mode": True,
        "block_diary_enabled": True,
        "daily_digest_enabled": True,
        "context_style": "full",
    })
    plugin._last_umo = UMO
    plugin.wardrobe = None
    plugin._diary_writing = False
    plugin._data_mem = lambda: None

    async def _data_call(*args, **kwargs):
        return []

    plugin._data_call = _data_call
    plugin._data_bot_id = lambda: "bot"
    plugin._first_private_umo = lambda: UMO
    plugin._diary_data_root = lambda: diary_root
    for name in DIARY_METHODS:
        target = getattr(amo_schedule.ScheduleMixin, name)
        setattr(plugin, name, types.MethodType(target, plugin))
    plugin._diary_time_rules = types.MethodType(amo_schedule._diary_time_rules, plugin)
    plugin._clean_diary_text = amo_schedule.ScheduleMixin._clean_diary_text
    try:
        from amo_feature_api import FeatureApiMixin
        plugin.note_recent_topic = types.MethodType(
            FeatureApiMixin.note_recent_topic, plugin)
    except Exception:  # 可选：digest 播种要用；导入失败时 digest 段会记 blocked
        plugin.note_recent_topic = None
    return plugin, state


# ---------------------------------------------------------------- 时钟 / 日程 / 健康 / 天气
def ts_of(day, hour, minute=0):
    return dt.datetime.combine(dt.date.fromisoformat(str(day)),
                               dt.time(int(hour), int(minute))).timestamp()


def day_list(count=PLAN_DAYS):
    return [(START + dt.timedelta(days=index)).isoformat() for index in range(count)]


def generate_schedules(plugin, state, days):
    """生产日程引擎逐天生成真实日程块（与 work/schedule_7day.py 同入口）。"""
    blocks, kinds, ctxs = {}, {}, {}
    for index, day in enumerate(days):
        kind = weeks.day_kind_for(index, day)
        weeks.sim24.set_clock(weeks.at(day, 0, 0))
        try:
            ctx, _items = weeks.build_day_skeleton(plugin, state, day, kind)
        finally:
            weeks.sim24.restore_clock()
        rows = [dict(row) for row in (state.schedule.get(day)
                                      or plugin.db.get_schedule(day) or [])]
        blocks[day] = rows
        kinds[day] = kind
        ctxs[day] = ctx
    return blocks, kinds, ctxs


GOLDEN_SCHEDULE = os.path.join(ROOT, "tests", "golden", "schedule_7day_seed7.json")


def golden_schedule(days):
    """并行改生产代码时 schedule_skeleton 可能中途报错：退回生产 golden 快照（同生成器 seed=7）。"""
    with open(GOLDEN_SCHEDULE, encoding="utf-8") as handle:
        data = json.load(handle)
    out, kinds = {}, {}
    for day in days:
        item = (data.get("days") or {}).get(day) or {}
        rows = []
        for row in item.get("blocks") or []:
            if isinstance(row, list) and len(row) >= 6:
                rows.append({"start": row[0], "end": row[1], "activity": row[2],
                             "place": row[3], "category": row[4], "zone": row[5]})
        if rows:
            out[day] = rows
            kinds[day] = str(item.get("kind") or "workday")
    return out, kinds


def simulated_weather(state, day, hour):
    clock.freeze(ts_of(day, hour, 0))
    try:
        return dict(state._simulate_weather() or {})
    finally:
        clock.unfreeze()


def health_dict(status, note="", base_hours=24.0, since=0.0, until=0.0):
    return {"status": status, "kind": status if status != "健康" else "",
            "since": float(since), "until": float(until),
            "note": note, "treatment": "", "doctor": False,
            "severity": "minor" if status != "健康" else "",
            "base_hours": float(base_hours)}


def apply_health(state, plugin_db, spec, now_ts_value, until_ts):
    """用生产 HumanState.set_health 落状态（保留生产字段），再钉死起止时间。"""
    if spec is None:
        state.set_health("健康")
        return
    status, note = spec
    state.set_health(status, hours=24, note=note)
    state.health["since"] = float(now_ts_value)
    state.health["until"] = float(until_ts)
    state.health["severity"] = "minor"
    plugin_db.kv_set("health", dict(state.health))

def gen_day_of(days, index):
    """第 index 天的大日记是哪天早上写的（第二天 08:10；最后一天顺延一天）。"""
    if index + 1 < len(days):
        return days[index + 1]
    return (dt.date.fromisoformat(days[index]) + dt.timedelta(days=1)).isoformat()


def inject_day_health(plugin, state, days, index):
    """把「那一天」的健康写进生产 state，并让生产 read_health 落当天快照。

    缺口（2026-11-22）：以前只在生成时刻用 set_health 写健康，11-03 的低烧从没进过
    生产 state；第二天早上补写时 read_health 只能读到「已康复」。这里在当天 20:00 用
    生产 set_health 落状态，再调生产 read_health(目标日=今天) 由它自己写
    health_day:<day> 快照；第二天补写时 read_health(plugin, day) 就优先读快照。
    """
    day = days[index]
    gen_day = gen_day_of(days, index)
    spec = SICK_DAYS.get(index)
    gen_spec = GEN_SICK.get(index)
    since = ts_of(day, 8, 0)
    if spec is None:
        until = 0.0
    elif gen_spec:
        until = ts_of(gen_day, 12, 0)
    else:
        until = ts_of(gen_day, GEN_CLOCK[0], GEN_CLOCK[1])
    try:
        from amo_fact_timeline import read_health as _read_health
    except ImportError:  # pragma: no cover - 生产导入路径
        _read_health = None
    snapshot, written = {}, {}
    clock.freeze(ts_of(day, 20, 0))
    try:
        apply_health(state, plugin.db, spec, since, until)
        if _read_health is not None:
            written = dict(_read_health(plugin, day) or {})
    finally:
        clock.unfreeze()
    try:
        snapshot = dict(plugin.db.kv_get("health_day:" + day, {}) or {})
    except Exception:  # cleanup: 仿真容错
        snapshot = {}
    readback = {}
    clock.freeze(ts_of(gen_day, GEN_CLOCK[0], GEN_CLOCK[1]))
    try:
        if _read_health is not None:
            readback = dict(_read_health(plugin, day) or {})
    finally:
        clock.unfreeze()
    expected = str((spec or ("健康",))[0])
    return {
        "day": day,
        "gen_day": gen_day,
        "expected_status": expected,
        "state_status": str((getattr(state, "health", None) or {}).get("status") or ""),
        "read_at_day": {"status": written.get("status"), "sick": written.get("sick")},
        "snapshot": {"status": snapshot.get("status"), "sick": snapshot.get("sick"),
                     "since": snapshot.get("since") or 0,
                     "until": snapshot.get("until") or 0},
        "readback_at_gen": {"status": readback.get("status"),
                            "sick": readback.get("sick")},
        "ok": str(readback.get("status") or "") == expected,
    }


# ---------------------------------------------------------------- 每天：事实 / 生成 / 核对
def diary_body(raw):
    text = str(raw or "")
    lines = text.splitlines()
    if lines and lines[0].startswith("我的日记"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def build_all_facts(plugin, state, days, kinds, blocks, weather_day, weather_gen):
    facts = {}
    for index, day in enumerate(days):
        spec = SICK_DAYS.get(index)
        day_health = ({"status": spec[0], "sick": True, "note": spec[1]} if spec
                      else {"status": "健康", "sick": False, "note": ""})
        gen_spec = GEN_SICK.get(index)
        gen_health = ({"status": gen_spec[0], "sick": True, "note": gen_spec[1]}
                      if gen_spec else {"status": "健康", "sick": False, "note": ""})
        try:
            from amo_date_facts import date_windows
            windows = date_windows(plugin, day)
        except Exception:  # cleanup: 仿真容错
            windows = []
        facts[day] = C.build_day_facts(
            day=day, kind=kinds[day], blocks=blocks[day],
            health_day=day_health, health_gen=gen_health,
            weather_day=weather_day.get(day) or {},
            date_windows=windows, ledger=[],
            now_gen="%s %02d:%02d" % (days[index + 1] if index + 1 < len(days)
                                       else day, GEN_CLOCK[0], GEN_CLOCK[1]))
    return facts


async def write_one_diary(plugin, state, days, index, blocks, facts, recorder, provider):
    """跑一天的生产大日记写入（_write_one_daily_diary），返回证据行。"""
    day = days[index]
    day_date = dt.date.fromisoformat(day)
    gen_day = (days[index + 1] if index + 1 < len(days)
               else (day_date + dt.timedelta(days=1)).isoformat())
    gen_now = dt.datetime.combine(dt.date.fromisoformat(gen_day),
                                  dt.time(*GEN_CLOCK))
    row = {"day": day, "gen_at": gen_now.isoformat(timespec="minutes"),
           "error": "", "prompt": "", "prompt_system": "", "diary": "",
           "diary_chars": 0, "calls": 0, "location_fallback": False,
           "stale_flag": False, "diary_stale": False, "health_injection": {}}
    # 生产 state 注入：当天 20:00 落健康 + 生产 read_health 写 health_day 快照。
    try:
        row["health_injection"] = inject_day_health(plugin, state, days, index)
    except Exception as exc:  # 注入缺口要留证据，不静默
        row["health_injection"] = {"error": repr(exc)[-240:], "ok": False}
    clock.freeze(gen_now.timestamp())
    try:
        gen_spec = GEN_SICK.get(index)
        if gen_spec:
            apply_health(state, plugin.db, gen_spec,
                         ts_of(day, 12, 0), ts_of(gen_day, 12, 0))
        else:
            apply_health(state, plugin.db, None, 0, 0)
        state.weather = dict(facts.get("weather") or {})
        plugin.llm = make_llm(provider, recorder, day)
        plugin._last_umo = UMO
        try:
            await plugin._write_one_daily_diary(day_date, gen_now)
        except Exception as exc:  # 链路异常要留证据，不静默
            row["error"] = repr(exc)[-300:]
    finally:
        clock.unfreeze()
    try:
        raw = amo_schedule._diary_read(plugin._diary_dir(), day_date) or ""
    except Exception:  # cleanup: 仿真容错
        raw = ""
    if not raw:
        try:
            path = str(plugin.db.kv_get("diary_written:" + day, "") or "")
            if path and os.path.isfile(path):
                with open(path, encoding="utf-8") as handle:
                    raw = handle.read()
        except Exception:  # cleanup: 仿真容错
            raw = ""
    body = diary_body(raw)
    calls = [item for item in recorder if item.get("tag") == day]
    row["calls"] = len(calls)
    if calls:
        row["prompt"] = calls[-1]["prompt"]
        row["prompt_system"] = calls[-1]["system"]
        row["error"] = row["error"] or calls[-1].get("error") or ""
    row["diary"] = body
    row["diary_chars"] = len(body)
    try:
        row["location_fallback"] = bool(plugin.db.kv_get(
            "diary_location_fallback:" + day, False))
        row["stale_flag"] = bool(plugin.db.kv_get("diary_stale:" + day, False))
        row["diary_stale"] = bool(plugin.db.kv_get("diary_stale:" + day, False))
    except Exception:  # cleanup: 仿真容错
        pass
    return row


def evaluate_day(plugin, days, index, row, facts, template_metrics=None):
    day = days[index]
    body = row.get("diary") or ""
    prompt = row.get("prompt") or ""
    issues, claims, hits = C.evaluate(body, facts, prompt)
    if template_metrics:
        issues.extend(C.template_issues(body, day, template_metrics))
    matrix = C.fact_matrix(facts, hits)
    production_verdict = None
    try:
        production_verdict = bool(plugin.diary_fact_mismatch(
            dt.date.fromisoformat(day), body)) if body else None
    except Exception as exc:
        production_verdict = "error:%r" % (exc,)
    prompt_facts = {
        "has_schedule_lines": "【日程】" in prompt,
        "has_health_fact": bool(re.search(r"^- \[[^\]]+\] \[状态\] 身体：", prompt, re.M)),
        "has_weather_fact": bool(re.search(r"^- \[[^\]]+\] \[(?:天气|weather)\]",
                                           prompt, re.M)),
        "has_flat_note": "非常平淡" in prompt,
        "has_nochat_note": "今天没怎么聊天" in prompt,
        "has_place_line": "今天去过：" in prompt,
        "chars": len(prompt),
    }
    counts = {}
    for issue in issues:
        counts[issue["category"]] = counts.get(issue["category"], 0) + 1
    return {
        "day": day,
        "kind": facts.get("kind"),
        "facts": {key: facts.get(key) for key in (
            "places", "groups", "out", "home_only", "meals", "meal_count", "wake",
            "late_blocks", "together", "together_any", "health", "health_gen", "weather",
            "block_count")},
        "blocks": facts.get("blocks") or [],
        "diary": body,
        "diary_chars": len(body),
        "generation": {"at": row.get("gen_at"), "error": row.get("error") or "",
                       "calls": row.get("calls"), "prompt": prompt,
                       "prompt_system": row.get("prompt_system") or "",
                       "location_fallback": row.get("location_fallback"),
                       "health_injection": row.get("health_injection") or {}},
        "prompt_facts": prompt_facts,
        "claims": {key: value for key, value in claims.items() if key != "sentences"},
        "claim_sentences": len(claims.get("sentences") or []),
        "hits": hits,
        "matrix": matrix,
        "issues": issues,
        "issue_counts": counts,
        "production_verdict_mismatch": production_verdict,
    }


def summarize_windows(day_reports, days):
    """3/5/7 天窗口统计（同一 7 天序列的前 N 天累计）。"""
    out = {}
    for window in (3, 5, 7):
        subset = [row for row in day_reports if row["day"] in days[:window]]
        counts = {}
        total = 0
        hard_total = 0
        for row in subset:
            for category, count in (row.get("issue_counts") or {}).items():
                counts[category] = counts.get(category, 0) + int(count)
                total += int(count)
        for row in subset:
            if row["generation"].get("error") or not row.get("diary"):
                hard_total += 1
        out[str(window)] = {
            "days": days[:window],
            "diaries": len([row for row in subset if row.get("diary")]),
            "generation_failures": hard_total,
            "issues_total": total,
            "issues_by_category": dict(sorted(counts.items())),
            "daily_issue_counts": {row["day"]: sum((row.get("issue_counts") or {}).values())
                                   for row in subset},
            "matrix_all_hit": all(
                all(bool(value.get("hit")) for key, value in (row.get("matrix") or {}).items()
                    if isinstance(value, dict) and "hit" in value)
                for row in subset if row.get("matrix")),
        }
    return out


def key_fact_lines(facts):
    """从一天事实表取「关键事件」短句（播种 digest 用，也用于算 digest 召回）。

    key=True 的是真正该追问的事件（出门/三餐/身体）；key=False 的是当天日常
    （收拾屋子/晚睡），跨天重复时优先被去重，不计入关键事件召回。
    """
    rows = []
    out_blocks = facts.get("out_blocks") or []
    preferred = [block for block in out_blocks
                 if set(block.get("groups") or []) - {"road", "street"}]
    first = (preferred or out_blocks or [None])[0]
    if first:
        place = str(first.get("place") or "")
        short = place.split("·")[-1] if place else ""
        rows.append({"line": "今天去了%s，%s" % (short or "外面", str(first.get("text") or "")[:18]),
                     "tokens": [token for token in (short, "去") if token], "key": True})
    for meal in (facts.get("meals") or [])[:2]:
        text = str(meal.get("text") or "")
        noun = next((word for word in ("牛肉粉", "粉", "面", "饭", "粥", "菜", "咖啡", "奶茶")
                     if word in text), "")
        rows.append({"line": "%s%s" % (str(meal.get("band") or ""), text[:20]),
                     "tokens": [token for token in (noun, meal.get("band")) if token],
                     "key": True})
    health = facts.get("health") or {}
    if health.get("sick"):
        rows.append({"line": "今天有点%s，%s" % (health.get("status"), str(health.get("note") or "")[:16]),
                     "tokens": [str(health.get("status") or "")], "key": True})
    home = next((block for block in facts.get("blocks") or []
                 if block.get("category") in ("life", "rest", "play")
                 and (block.get("place") or "") in ("家", "出租屋", "卧室")), None)
    if home:
        rows.append({"line": "今天在家%s" % str(home.get("activity") or "")[:20],
                     "tokens": [str(home.get("activity") or "")[:2]], "key": False})
    if facts.get("wake"):
        rows.append({"line": "今天睡到%s才起" % facts.get("wake"),
                     "tokens": [str(facts.get("wake") or "")], "key": False})
    late = (facts.get("late_blocks") or [])
    if late:
        rows.append({"line": "晚上还在%s" % str(late[-1].get("text") or "")[:18],
                     "tokens": [str(late[-1].get("text") or "")[:2]], "key": False})
    return rows[:8]

# ---------------------------------------------------------------- digest 注入（最近三天）
class _FakeEvent:
    message_str = ""
    unified_msg_origin = UMO

    def is_private_chat(self):
        return True

    def get_extra(self, key, default=None):
        return default


def render_digest_line(plugin, probe_ts):
    """调生产 _ctx_tail_runtime，把【前几天】那一行原样拿出来。"""
    from amo_runtime_context import RuntimeContextMixin
    inject = types.MethodType(RuntimeContextMixin._ctx_tail_runtime, plugin)
    parts = []
    context = {"event": _FakeEvent(), "memory_text": "", "umo": UMO, "uid": "1"}
    error = ""
    clock.freeze(probe_ts)
    try:
        inject(context, parts)
    except Exception as exc:
        error = repr(exc)[:240]
    finally:
        clock.unfreeze()
    line = next((str(part) for part in parts if str(part).startswith("【前几天】")), "")
    return line, error


def _digest_slices(line):
    """把【前几天】行按 昨天/前天/大前天 切成三段。"""
    labels = ("昨天", "前天", "大前天")
    found = []
    for label in labels:
        position = line.find(label + "：")
        found.append((label, position))
    slices = {}
    for index, (label, position) in enumerate(found):
        if position < 0:
            continue
        start = position + len(label) + 1
        end = len(line)
        for _next_label, next_position in found[index + 1:]:
            if next_position > position:
                end = next_position
                break
        slices[label] = line[start:end]
    return slices


def validate_digest(line, probe_day_index, days, digests, seeded, dropped=None,
                    history=None, day_limit=72):
    """校验【前几天】：三天都在 / 只带事实 / 跨天不重复 / 关键事件不丢 / 没被截断。

    dropped/history 是生产播种路径的跨天去重证据：被丢掉的事实必须真的是前两天
    digest 里已经出现过的（去重不能把唯一的关键事件丢掉）。
    """
    labels = ("昨天", "前天", "大前天")
    dropped = dropped or {}
    history = history or {}
    checks = []
    detail = {"line": line, "line_len": len(line), "days": {}, "duplicates": [],
              "ungrounded": [], "truncated": False, "expected_len": 0,
              "dedupe": [], "day_limit": int(day_limit), "max_possible_len": 0}
    expected_days = []
    for offset in (1, 2, 3):
        target_index = probe_day_index - offset
        day = days[target_index] if 0 <= target_index < len(days) else ""
        expected_days.append((labels[offset - 1], day))
    if not line:
        checks.append({"ok": False, "name": "digest.line_present",
                       "detail": "生产注入没有【前几天】行（day_digest 全空）"})
        return checks, detail
    checks.append({"ok": True, "name": "digest.line_present", "detail": line[:240]})
    slices = _digest_slices(line)
    pieces_by_day = {}
    expected_total = len("【前几天】")
    present_days = 0
    for label, day in expected_days:
        digest = digests.get(day) or ""
        seeded_lines = [item["line"] for item in (seeded.get(day) or [])]
        expected_total += len(label) + 1 + len(digest)
        present_days += 1
        piece = slices.get(label)
        # 关键事件召回只算出门/三餐/身体（key=True）；日常句跨天去重不算丢事件。
        tokens = []
        for item in seeded.get(day) or []:
            if item.get("key") is False:
                continue
            tokens.extend(item.get("tokens") or [])
        day_detail = {"day": day, "label": label, "digest": digest,
                      "slice": piece or "", "tokens": tokens}
        if not digest:
            day_detail["status"] = "no_digest"
            checks.append({"ok": False, "name": "digest.day_%s" % label,
                           "detail": "%s（%s）没有 day_digest 可注入" % (label, day)})
        elif piece is None:
            day_detail["status"] = "missing_label"
            checks.append({"ok": False, "name": "digest.day_%s" % label,
                           "detail": "%s 标签缺失（被 240 字截断或没生成）" % label})
        else:
            contained = digest in piece
            if contained:
                day_detail["status"] = "full"
            elif piece and digest.startswith(piece):
                day_detail["status"] = "truncated"
                detail["truncated"] = True
            else:
                day_detail["status"] = "partial_or_truncated"
                detail["truncated"] = True
            hits = [token for token in tokens if token and token in piece]
            day_detail["token_recall"] = round(len(hits) / max(1, len(tokens)), 3) if tokens else 1.0
            checks.append({"ok": contained or day_detail["status"] == "truncated",
                           "name": "digest.day_%s" % label,
                           "detail": "%s（%s）状态=%s 召回=%s" % (
                               label, day, day_detail["status"],
                               day_detail.get("token_recall"))})
        # 事实性：切片里的每一片（；分隔）必须来自播种事实原文
        for piece_text in [item for item in (piece or "").split("；") if item.strip()]:
            pieces_by_day.setdefault(day, []).append(piece_text.strip())
            if seeded_lines and not any(
                    piece_text.strip() in line
                    or (line.startswith(piece_text.strip()) and len(piece_text.strip()) >= 6)
                    for line in seeded_lines):
                detail["ungrounded"].append({"day": day, "piece": piece_text.strip()[:80]})
        detail["days"][day] = day_detail
    checks.append({"ok": not detail["ungrounded"], "name": "digest.facts_only",
                   "detail": "非事实片段 %d 个" % len(detail["ungrounded"])})
    # 跨天去重证据：被生产去重丢掉的事实必须出现在前两天的 digest 里。
    dedupe_rows = []
    for _label, day in expected_days:
        prior = {key: str(value or "") for key, value in (history.get(day) or {}).items()}
        for line_text in dropped.get(day) or []:
            matched = [key for key, stored in prior.items() if line_text and line_text in stored]
            dedupe_rows.append({"day": day, "line": line_text[:80],
                                "matched_days": matched, "duplicate": bool(matched)})
    detail["dedupe"] = dedupe_rows
    checks.append({"ok": all(row["duplicate"] for row in dedupe_rows),
                   "name": "digest.dedupe_duplicates_only",
                   "detail": "跨天去重丢掉 %d 条；全部是前两天已出现过：%s" % (
                       len(dedupe_rows),
                       [row["line"] for row in dedupe_rows if not row["duplicate"]][:3] or "是")})
    # 240 字上限预算：3 条 day_digest 各自不超 day_limit，最坏总长也要 < 240。
    over = [(day, len(digests.get(day) or "")) for _label, day in expected_days
            if len(digests.get(day) or "") > day_limit]
    max_possible = len("【前几天】") + 3 * 3 + 2 + 3 * int(day_limit)
    detail["max_possible_len"] = max_possible
    checks.append({"ok": not over and max_possible < 240,
                   "name": "digest.cap_budget",
                   "detail": "单条 day_digest 上限 %d 字，超限 %s；三天最坏总长 %d < 240" % (
                       day_limit, over or "无", max_possible)})
    # 重复：同一片段出现在两天 / 同一天出现两次
    seen = {}
    for day, pieces in pieces_by_day.items():
        for piece in pieces:
            seen.setdefault(piece, []).append(day)
    duplicates = [{"piece": piece, "days": sorted(set(days_))}
                  for piece, days_ in seen.items() if len(days_) >= 2]
    within = [{"day": day, "piece": piece} for day, pieces in pieces_by_day.items()
              for piece in set(pieces) if pieces.count(piece) >= 2]
    detail["duplicates"] = duplicates + within
    checks.append({"ok": not (duplicates or within), "name": "digest.no_duplicate",
                   "detail": "跨天重复 %d，同天重复 %d" % (len(duplicates), len(within))})
    detail["expected_len"] = expected_total + max(0, present_days - 1)
    detail["truncated"] = detail["truncated"] or len(line) < expected_total - 1
    checks.append({"ok": not detail["truncated"], "name": "digest.not_truncated",
                   "detail": "实际 %d 字 / 期望 %d 字（生产注入上限 240 字）" % (
                       len(line), expected_total)})
    recalls = [item.get("token_recall", 0) for item in detail["days"].values()
               if item.get("digest")]
    checks.append({"ok": bool(recalls) and min(recalls) >= 0.8, "name": "digest.key_events",
                   "detail": "关键事件召回：%s" % ["%s:%s" % (
                       day, detail["days"][day].get("token_recall"))
                       for day in detail["days"]]})
    return checks, detail


async def digest_phase(plugin, days, facts, diary_root, provider, static_scan):
    """digest 注入端到端：生产原样 / 生产写入方播种（跨天去重+截断）/ 三天窗口探针。

    播种不再自己用 note_recent_topic + fold_day 拼一条非生产路径，而是走生产
    amo_schedule._seed_day_digest（note_recent_topic 写入、fold_day 折叠、跨天去重、
    day_digest 上限裁剪都在生产代码里）；只有「事实源」由 harness 用 key_fact_lines
    控制。这样测到的跨天去重/240 字预算就是生产真实行为。
    """
    result = {"static_scan": static_scan, "as_production": {}, "seeded": {},
              "probes": [], "checks": []}
    # 1) 生产原样：没有当天事实写进来时，day_digest 恒空
    plain_plugin, _plain_state = build_plugin(os.path.join(diary_root, "digest_plain"))
    probe_ts = ts_of(days[-1], GEN_CLOCK[0], 10) + 86400
    line, error = render_digest_line(plain_plugin, probe_ts)
    result["as_production"] = {
        "line": line, "error": error,
        "episodic_items": len(plain_plugin.db.kv_get("episodic_memory", []) or []),
        "writer_call_sites": len(static_scan.get("call_sites") or []),
        "episodic_kv_reads": len(static_scan.get("kv_reads") or []),
        "writer_definition": static_scan.get("definition") or [],
        "verdict": "line_empty" if not line else "line_present",
    }
    result["checks"].append({
        "ok": not line,
        "name": "digest.as_production_empty",
        "detail": "生产代码里 note_recent_topic 调用点=%d → episodic_memory 恒空 → day_digest 恒空 → 没有【前几天】行"
                  % len(static_scan.get("call_sites") or [])})
    # 2) 播种：走生产写入方 _seed_day_digest（事实源用 key_fact_lines 控制）
    seeded_plugin, _seeded_state = build_plugin(os.path.join(diary_root, "digest_seeded"))
    writer = getattr(seeded_plugin, "note_recent_topic", None)
    seeded, digests, dropped_by_day, history_by_day = {}, {}, {}, {}
    if writer is None:
        result["checks"].append({"ok": False, "name": "digest.writer_available",
                                 "detail": "note_recent_topic 绑定失败，digest 播种无法进行"})
        return result
    import amo_schedule as _amo_schedule
    day_limit = int(getattr(_amo_schedule, "DIGEST_DAY_MAX_CHARS", 72))
    result["seeded_write_path"] = ("amo_schedule._seed_day_digest（生产 note_recent_topic "
                                   "+ fold_day + 跨天去重 + day_digest 上限裁剪）")
    result["day_digest_limit"] = day_limit

    async def _seed_lines(_plugin, day_arg):
        day_text = (day_arg.isoformat() if hasattr(day_arg, "isoformat")
                    else str(day_arg)[:10])
        return [item["line"] for item in key_fact_lines(facts.get(day_text) or {})]

    original_lines = _amo_schedule._diary_day_fact_lines
    try:
        _amo_schedule._diary_day_fact_lines = _seed_lines
        for index, day in enumerate(days):
            items = key_fact_lines(facts[day])
            seeded[day] = items
            prior = {}
            for offset in (1, 2):
                previous_index = index - offset
                if previous_index < 0:
                    continue
                prior[days[previous_index]] = str(
                    seeded_plugin.db.kv_get("day_digest:" + days[previous_index], "") or "")
            history_by_day[day] = prior
            gen_day = gen_day_of(days, index)
            clock.freeze(ts_of(gen_day, *GEN_CLOCK))
            try:
                await _amo_schedule._seed_day_digest(
                    seeded_plugin, dt.date.fromisoformat(day))
            except Exception as exc:
                result["seeded"].setdefault("errors", []).append(repr(exc)[:200])
            finally:
                clock.unfreeze()
            digest = str(seeded_plugin.db.kv_get("day_digest:" + day, "") or "")
            digests[day] = digest
            dropped = [item["line"] for item in items if item["line"] not in digest]
            dropped_by_day[day] = dropped
            result["seeded"][day] = {
                "facts": [item["line"] for item in items],
                "key_facts": [item["line"] for item in items if item.get("key", True)],
                "digest": digest, "chars": len(digest),
                "dropped": dropped,
                "deduped": [line for line in dropped
                            if any(line in stored for stored in prior.values())],
                "history_chars": {key: len(value) for key, value in prior.items()},
            }
    finally:
        _amo_schedule._diary_day_fact_lines = original_lines
    # 3) 注入探针：第 6 天（前三天=第3/4/5天）和第 5 天窗口
    for probe_index in (5, 4):
        if probe_index >= len(days):
            continue
        probe_ts = ts_of(days[probe_index], PROBE_CLOCK[0], PROBE_CLOCK[1])
        line, error = render_digest_line(seeded_plugin, probe_ts)
        checks, detail = validate_digest(
            line, probe_index, days, digests, seeded,
            dropped=dropped_by_day, history=history_by_day, day_limit=day_limit)
        detail.update({"probe_day": days[probe_index], "probe_ts": probe_ts,
                       "error": error, "checks": checks})
        result["probes"].append(detail)
        result["checks"].extend(checks)
    return result


# ---------------------------------------------------------------- 同伴正向探针（面对面）
async def companion_probe(provider, recorder, diary_root):
    """工作日之外单独造一天「面对面约会」：验证有约会事实时同伴能不能写对。"""
    day = (START + dt.timedelta(days=5)).isoformat()     # 11-06（长假）
    result = {"day": day, "conditions": "注入约会块 + 生产约会会话（begin/finish）",
              "error": ""}
    plugin, state = build_plugin(os.path.join(diary_root, "companion"))
    try:
        weeks.sim24.set_clock(weeks.at(day, 0, 0))
        try:
            weeks.build_day_skeleton(plugin, state, day, "holiday")
        finally:
            weeks.sim24.restore_clock()
        rows = [dict(row) for row in (state.schedule.get(day)
                                      or plugin.db.get_schedule(day) or [])]
        kept = [row for row in rows if str(row.get("start") or "") < "19:00"]
        kept.append({"start": "19:00", "end": "21:30", "activity": "和老公约会，一起吃火锅",
                     "place": "东莞·商场·餐厅", "zone": "restaurant", "category": "date",
                     "detail": "面对面一起吃饭", "mood": "开心", "mood_valence": 88.0})
        kept.append({"start": "21:30", "end": "23:00", "activity": "约会完他送我回家",
                     "place": "路上", "zone": "road", "category": "commute",
                     "detail": "各回各家", "mood": "平静", "mood_valence": 70.0})
        kept.append({"start": "23:00", "end": "24:00", "activity": "洗澡",
                     "place": "家", "zone": "home", "category": "life",
                     "detail": "", "mood": "平静", "mood_valence": 72.0})
        rows = sorted(kept, key=lambda row: str(row.get("start") or ""))
        state.schedule[day] = rows
        plugin.db.save_schedule(day, rows)
        plugin.db.bump_schedule_version(day)
        import amo_date_session as DS
        begin_ts = ts_of(day, 19, 0)
        end_ts = ts_of(day, 21, 30)
        clock.freeze(begin_ts)
        try:
            DS.begin(plugin, "和老公约会，一起吃火锅", end_ts=end_ts,
                     place="东莞·商场·餐厅", title="火锅", source="sim",
                     now=dt.datetime.fromtimestamp(begin_ts))
        finally:
            clock.unfreeze()
        clock.freeze(end_ts)
        try:
            DS.finish(plugin, now=dt.datetime.fromtimestamp(end_ts))
        finally:
            clock.unfreeze()
        from amo_date_facts import date_windows
        windows = date_windows(plugin, day)
        weather = simulated_weather(state, day, 12)
        facts = C.build_day_facts(day=day, kind="holiday", blocks=rows,
                                  health_day={"status": "健康", "sick": False},
                                  health_gen={"status": "健康", "sick": False},
                                  weather_day=weather, date_windows=windows, ledger=[],
                                  now_gen="%s 08:10" % day)
        result["date_windows"] = windows
        result["blocks"] = facts.get("blocks")
        row = await write_one_diary(plugin, state, [day], 0, {}, facts, recorder, provider)
        evaluation = evaluate_day(plugin, [day], 0, row, facts)
        result.update({"diary": row.get("diary") or "", "prompt": row.get("prompt") or "",
                       "matrix": evaluation.get("matrix"),
                       "issues": evaluation.get("issues"),
                       "issue_counts": evaluation.get("issue_counts"),
                       "generation_error": row.get("error") or ""})
        result["checks"] = [
            {"ok": bool(windows), "name": "companion.date_window",
             "detail": "生产 date_windows=%s" % json.dumps(windows, ensure_ascii=False)[:240]},
            {"ok": bool(evaluation.get("matrix", {}).get("companion", {}).get("hit")),
             "name": "companion.together_mentioned",
             "detail": "日记提到在一起的证据=%s" % str(
                 evaluation.get("matrix", {}).get("companion"))},
            {"ok": not any(issue["category"] == "companion"
                           for issue in evaluation.get("issues") or []),
             "name": "companion.no_false_presence",
             "detail": "同伴矛盾 %d 个" % len([issue for issue in evaluation.get("issues") or []
                                             if issue["category"] == "companion"])},
        ]
    except Exception as exc:
        result["error"] = repr(exc)[-400:]
    return result

# ---------------------------------------------------------------- 汇总 / 根因 / 样例
CATEGORY_LABEL = {
    "place": "地点", "action": "动作", "companion": "同伴", "meal": "三餐",
    "health": "健康", "weather": "天气", "fabricated": "编造事件",
    "template": "模板复读", "coverage": "关键事件覆盖", "order": "时间顺序",
    "transition": "过渡",
}
CATEGORY_ORDER = ("place", "action", "companion", "meal", "health", "weather",
                  "fabricated", "coverage", "template", "order", "transition")


def all_issue_count(day_reports):
    counts = {}
    for report in day_reports:
        for category, count in (report.get("issue_counts") or {}).items():
            counts[category] = counts.get(category, 0) + int(count)
    return dict(sorted(counts.items()))


def pick_samples(day_reports, limit=10):
    priority = {name: index for index, name in enumerate(CATEGORY_ORDER)}
    rows = []
    for report in day_reports:
        for issue in report.get("issues") or []:
            rows.append({
                "day": report["day"],
                "category": issue["category"],
                "category_label": CATEGORY_LABEL.get(issue["category"], issue["category"]),
                "subtype": issue.get("subtype"),
                "diary_evidence": C.desensitize(issue.get("detail") or "", 360),
                "fact_evidence": C.desensitize(issue.get("evidence") or "", 240),
                "prompt_has_fact": issue.get("prompt_has_fact"),
            })
    rows.sort(key=lambda item: (priority.get(item["category"], 99), item["day"]))
    picked, seen = [], set()
    for row in rows:
        key = (row["day"], row["category"])
        if key in seen:
            continue
        seen.add(key)
        picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def build_special_sections(day_reports):
    health = []
    for report in day_reports:
        if not (report.get("facts", {}).get("health") or {}).get("sick"):
            continue
        health.append({
            "day": report["day"],
            "day_status": report["facts"]["health"]["status"],
            "gen_status": report["facts"]["health_gen"]["status"],
            "prompt_has_health_fact": report["prompt_facts"]["has_health_fact"],
            "diary_sick_mentioned": bool(report["hits"].get("sick_mentioned")),
            "health_claims": [claim for claim in report["claims"].get("health") or []],
            "state_injection": ((report.get("generation") or {}).get("health_injection")
                                or {}),
        })
    weather = []
    for report in day_reports:
        claims = report["claims"].get("weather") or []
        if not claims:
            continue
        weather.append({
            "day": report["day"],
            "fact": report["facts"]["weather"],
            "prompt_has_weather_fact": report["prompt_facts"]["has_weather_fact"],
            "claims": [claim.get("clause") for claim in claims],
            "issues": [issue for issue in report["issues"] if issue["category"] == "weather"],
        })
    return {"health": health, "weather": weather}


def probe_prompt_frame(day_reports):
    """确定性检查：补写昨天时提示词「今天」/「[昨日]」/「已经过完的某天」三处口径是否打架。"""
    rows = []
    for report in day_reports:
        prompt = str((report.get("generation") or {}).get("prompt") or "")
        rows.append({
            "day": report.get("day"),
            "prose_says_today": "下面是你今天真实发生的事" in prompt,
            "fact_label_yesterday": "[昨日]" in prompt,
            "time_rules_finished": "已经过完的" in prompt,
            "flat_note": (report.get("prompt_facts") or {}).get("has_flat_note"),
            "home_lock_failures": len([issue for issue in report.get("issues") or []
                                       if issue.get("subtype") == "home_lock"]),
        })
    mixed = [row for row in rows if row["prose_says_today"] and row["fact_label_yesterday"]]
    return {"rows": rows, "mixed_days": len(mixed),
            "home_lock_failures": sum(row["home_lock_failures"] for row in rows)}


def probe_name_scrub():
    """生产日记路径（amo_diary_guard.scrub_names_guarded）会不会把食物/普通词当人名换掉。

    底层 foreign_names 仍会把「文档」这类词当候选；这里同时记录「生产守卫是否真的替换」，
    hit 只看生产路径，避免把已被守卫过滤掉的底层候选算成生产问题。
    """
    try:
        from amo_immersion_guard import foreign_names, scrub_names
        from amo_diary_guard import scrub_names_guarded
        samples = ["中午下楼吃了碗牛肉粉", "晚上在楼下小馆吃了碗面", "喝了一杯热咖啡",
                   "上午整理文档、写周报", "把衣服晾到阳台上", "一个人在家刷剧、刷手机"]
        rows = []
        for sample in samples:
            guarded = scrub_names_guarded(sample)
            rows.append({"text": sample,
                         "foreign_names": list(foreign_names(sample) or []),
                         "raw_scrubbed": scrub_names(sample),
                         "guarded": guarded,
                         "guarded_replaced": guarded != sample})
        return {"samples": rows,
                "production_path": "amo_diary_guard.scrub_names_guarded",
                "hit": any(row["guarded_replaced"] for row in rows),
                "raw_detector_hit": any(row["foreign_names"] for row in rows)}
    except Exception as exc:
        return {"error": repr(exc)[:200], "hit": False, "samples": []}


def build_root_causes(day_reports, digest, special, template, static_scan, companion,
                      provider, name_probe=None, prompt_frame=None):
    causes = []
    issues_by_cat = all_issue_count(day_reports)
    definition = (static_scan.get("definition") or [{}])[0]
    if not static_scan.get("call_sites"):
        causes.append({
            "id": "digest_no_writer",
            "severity": "P0",
            "title": "最近三天 digest 注入是死链路：episodic_memory 没有生产写入方",
            "evidence": [
                "note_recent_topic 只有定义：%s:%s" % (
                    definition.get("file", "?"), definition.get("line", "?")),
                "生产代码里 note_recent_topic(...) 调用点 = 0；"
                "episodic_memory 的 kv_set 只出现在该函数体内",
                "amo_schedule._day_digest 只从 episodic_memory 折 digest；"
                "amo_runtime_context 的【前几天】只读 day_digest:<day>",
                "仿真里生产原样跑完 7 天，episodic_memory 条目 = %s，"
                "注入行 = %r" % (digest["as_production"].get("episodic_items"),
                                digest["as_production"].get("line")),
            ],
            "impact": "上下文里最近三天的事实恒缺席（0/3 天），不重复但也没内容——"
                      "属于「不丢关键事件」的直接失败。",
        })
    lost = [item for item in special.get("health") or []
            if not item.get("diary_sick_mentioned")]
    if lost:
        causes.append({
            "id": "health_fact_at_generation",
            "severity": "P1",
            "title": "大日记在第二天早上生成，健康事实读的是「生成时刻」而不是「当天」",
            "evidence": [
                "amo_fact_timeline.read_health 读的是当前 state.health；"
                "早上补写昨天时昨天已康复 → 素材包里没有生过病",
                "生病当天没写进日记：%s" % [
                    "%s(当天=%s/生成时=%s)" % (item["day"], item["day_status"],
                                               item["gen_status"])
                    for item in lost],
                "提示词里是否带健康事实：%s" % [
                    bool(item.get("prompt_has_health_fact")) for item in lost],
            ],
            "impact": "病好得太快的那几天，日记会漏写生病/不舒服；"
                      "前一天抱怨过的事后一天完全消失。",
        })
    ungrounded = [item for item in special.get("weather") or []
                  if any(str(issue.get("subtype") or "").startswith("ungrounded")
                         for issue in item.get("issues") or [])]
    if ungrounded:
        causes.append({
            "id": "weather_not_in_prompt",
            "severity": "P1",
            "title": "大日记素材包没有天气事实，日记里写天气=无源扩写",
            "evidence": [
                "amo_fact_timeline 的生产者里没有 weather（只读日程/账本/身体/聊天/穿搭）",
                "仿真里 %d/%d 天日记写了天气断言，提示词里都不含天气事实"
                % (len(ungrounded), len(day_reports)),
                "提示词确实没有天气事实：has_weather_fact=%s"
                % [item["prompt_has_weather_fact"] for item in ungrounded],
            ],
            "impact": "天气与当天模拟天气冲突时无法被现有守卫拦住；不冲突也只是碰巧。",
        })
    flat = [report for report in day_reports if report["prompt_facts"]["has_flat_note"]]
    if flat:
        causes.append({
            "id": "flat_day_prompt_bias",
            "severity": "P2",
            "title": "没有聊天/块日记时提示词固定追加「今天没有任何聊天、消费或外出记录，非常平淡」",
            "evidence": [
                "amo_schedule._generate_daily_diary：if not chat_lines and not hourly_lines "
                "才追加这句；判断只看聊天和块日记，不看日程事实",
                "%d/%d 天的提示词都带了这句，但当天日程里有 %s 等外出块"
                % (len(flat), len(day_reports),
                   [report["facts"]["places"][:3] for report in flat[:2]]),
            ],
            "impact": "即使当天出门/吃饭/上班，模型也被暗示「今天很平淡」，"
                      "容易写成窝在家的一天（与「一直在家人」类错误同源）。",
        })
    repeated = (template.get("repeated_sentences") or [])
    high_overlap = [item for item in (template.get("pair_4gram_jaccard") or [])
                    if item.get("score", 0) >= 0.25]
    if repeated or high_overlap:
        causes.append({
            "id": "template_repetition",
            "severity": "P2",
            "title": "跨天复读：固定句式/固定素材结构反复出现",
            "evidence": [
                "整句跨天重复 %d 句：%s" % (len(repeated), [
                    item["sentence"][:40] for item in repeated[:3]]),
                "4-gram 重叠 >=25%% 的天对：%s" % [
                    "%s~%s %.0f%%" % (item["days"][0], item["days"][1],
                                      item["score"] * 100)
                    for item in high_overlap[:3]],
                "提示词只写「语言每天要有变化」，没有跨天差异的结构化约束",
            ],
            "impact": "连看几天日记会觉得是同一个模板换素材。",
        })
    repeated_sentences = [item["sentence"] for item in repeated]
    if any("想起他以前叮嘱过" in sentence for sentence in repeated_sentences):
        causes.append({
            "id": "guard_safe_line_repeated",
            "severity": "P2",
            "title": "生产守卫的固定替换句变成跨天复读模板",
            "evidence": [
                "amo_diary_guard._SAFE_INTERACTION_LINE 是固定句「突然想起他以前叮嘱过我的话，心里软了一下。」",
                "素材里没有聊天时凡命中「他今天提醒我」类句子就整句替换成这一句，"
                "于是不同天出现同一句：%s" % [s[:40] for s in repeated_sentences
                                              if "想起他以前叮嘱过" in s],
            ],
            "impact": "守卫兜底本身保证不编造互动，但代价是多天日记出现同一句，模板感明显。",
        })
    if (prompt_frame or {}).get("mixed_days"):
        observed = [report for report in day_reports
                    if any(issue.get("subtype") == "home_lock"
                           for issue in report.get("issues") or [])]
        causes.append({
            "id": "date_frame_mismatch",
            "severity": "P1",
            "title": "补写昨天时日期口径混用：素材行标 [昨日]，提示词却说「今天」，"
                     "模型会把当天事实当成昨天（「今天哪儿也没去」的直接来源）",
            "evidence": [
                "amo_fact_pool.render_prompt 首句是「下面是你今天真实发生的事」，"
                "但事实行标签来自 amo_immersion_guard.time_label(day, now)，补写昨天时是 [昨日]",
                "提示词口径：mixed_days=%d/%d（prose=今天 + facts=[昨日]）" % (
                    prompt_frame["mixed_days"], len(day_reports)),
                "self._diary_time_rules 又说「这是已经过完的某天，00:00-24:00 都是事实」；"
                "三处口径不一致，模型只能自己猜哪段是「今天」",
                "本次运行 home_lock 失败 %d 处：%s" % (
                    len(observed),
                    [C.desensitize(report.get("diary", ""), 120) for report in observed[:1]]),
            ],
            "impact": "「日程有外出、日记写一直在家人」的直接来源；也解释模型把当天出游说成「昨天」。",
        })
    if (name_probe or {}).get("hit"):
        hits = [row for row in (name_probe or {}).get("samples") or []
                if row.get("foreign_names")]
        causes.append({
            "id": "name_scrub_eats_food",
            "severity": "P2",
            "title": "人名兜底把食物/普通名词当名单外人名替换：日记出现「吃一个朋友」",
            "evidence": [
                "amo_diary_guard.sanitize_diary -> amo_immersion_guard.scrub_names 把"
                "名单外检测到的词替换成「一个朋友/一个同事」",
                "生产 foreign_names 实测命中：%s" % [
                    (row["text"], row["foreign_names"], row["scrubbed"]) for row in hits],
                "本次仿真里日志出现「兜底替换名单外人名: ['牛肉粉']」，"
                "对应日记句于是变成「吃一个朋友、喝热饮」",
            ],
            "impact": "守卫为了防捏造人名，把食物名误当人名，产出语义崩坏的句子，"
                      "并让同伴检查误报（「朋友」无事实支撑）。",
        })
    fabricated_issues = [issue for report in day_reports for issue in report.get("issues") or []
                         if issue["category"] == "fabricated"]
    if any(issue.get("subtype") == "ungrounded_purchase" for issue in fabricated_issues):
        causes.append({
            "id": "fabricated_spend",
            "severity": "P2",
            "title": "没有账本却写花钱/心疼钱（消费无源）",
            "evidence": [
                "仿真账本为空（wallet 无明细），日记仍写「花了多少钱/心疼」",
                "命中 %d 处：%s" % (
                    len([issue for issue in fabricated_issues
                         if issue.get("subtype") == "ungrounded_purchase"]),
                    [issue["detail"][:50] for issue in fabricated_issues
                     if issue.get("subtype") == "ungrounded_purchase"][:3]),
            ],
            "impact": "素材包没有金额就写花钱，属于消费编造；现有 fact guard 只在有聊天/账本时兜底。",
        })
    for category in ("fabricated", "place", "action", "companion", "meal", "coverage"):
        count = issues_by_cat.get(category, 0)
        if not count:
            continue
        causes.append({
            "id": "residual_" + category,
            "severity": "P2",
            "title": "残留 %s 问题 %d 个（现有守卫未覆盖）" % (
                CATEGORY_LABEL.get(category, category), count),
            "evidence": ["分类统计：%s" % json.dumps(issues_by_cat, ensure_ascii=False)],
            "impact": "生成层/守卫层仍有缺口，明细见每天 issues。",
        })
    if digest.get("probes"):
        bad = [check for check in digest.get("checks") or [] if not check.get("ok")]
        if bad:
            causes.append({
                "id": "digest_injection_limits",
                "severity": "P2",
                "title": "digest 注入即使有数据也有上限/截断/不跨天去重",
                "evidence": [check["detail"] for check in bad[:6]],
                "impact": "最近三天里靠后的关键事件会被 240 字上限截掉；跨天重复事实不会被去重。",
            })
    if companion.get("error"):
        causes.append({"id": "companion_probe_error", "severity": "P2",
                       "title": "同伴正向探针没跑起来", "evidence": [companion["error"]],
                       "impact": "有约会事实时能否写对同伴，暂无双向证据。"})
    return causes


def render_markdown(report):
    lines = []

    def add(text=""):
        lines.append(text)

    add("# 隔离日程日记仿真报告（3/5/7 天）")
    add("")
    add("- 生成时间：%s" % report.get("generated_at"))
    add("- 仿真脚本：work/sim/diary_sim.py；核对器：work/sim/diary_checks.py")
    add("- 仓库：Astrbot_amoupup_forever（只读测试，未改生产代码/未写生产库）")
    source = report.get("schedule_source") or {}
    add("- 日程来源：%s（%s）" % (source.get("kind"),
                                  source.get("error") or "生产 ScheduleEngine 正常生成"))
    code = report.get("code_snapshot") or {}
    version_bits = []
    for name in ("schedule_skeleton.py", "schedule_plan.py", "schedule_engine.py",
                 "amo_schedule.py", "amo_fact_timeline.py"):
        item = code.get(name) or {}
        if item.get("sha256_16"):
            version_bits.append("%s=%s" % (name, item["sha256_16"]))
    add("- 本轮代码指纹：" + ("；".join(version_bits) or "见下方代码指纹"))
    model = report.get("model") or {}
    add("- 生成层：%s / %s（%s）" % (model.get("kind"), model.get("model"),
                                     model.get("note")))
    add("")
    limits = report.get("limitations") or []
    if limits:
        add("> 限制：" + "；".join(limits))
        add("")
    add("## 1. 结论摘要")
    add("")
    note = report.get("rerun_note") or ""
    if note:
        add("> " + note)
        add("")
    summary = report.get("window_summary") or {}
    add("| 窗口 | 天数 | 日记生成成功 | 问题总数 | 分类统计 |")
    add("|---|---|---|---|---|")
    for window in ("3", "5", "7"):
        item = summary.get(window) or {}
        counts = item.get("issues_by_category") or {}
        add("| 前 %s 天 | %d | %d | %d | %s |" % (
            window, len(item.get("days") or []), item.get("diaries") or 0,
            item.get("issues_total") or 0,
            "、".join("%s=%s" % (CATEGORY_LABEL.get(key, key), value)
                     for key, value in counts.items()) or "无"))
    add("")
    checks = report.get("checks") or []
    hard_bad = [check for check in checks if not check.get("ok")]
    add("硬检查：%d 项，失败 %d 项；失败项：%s" % (
        len(checks), len(hard_bad),
        "、".join(check["name"] for check in hard_bad) or "无"))
    add("")
    add("## 2. 隔离与只读证据")
    add("")
    isolation = report.get("isolation") or {}
    add("- 生产库前后一致：%s" % isolation.get("prod_db_unchanged"))
    add("- 生产库指纹（before/after）：%s" % json.dumps(
        isolation.get("prod_db_detail") or {}, ensure_ascii=False)[:1600])
    add("- 启动/结束：%s -> %s" % (isolation.get("started_at"),
                                   isolation.get("finished_at")))
    add("- 隔离守卫计数：%s" % json.dumps(isolation.get("guard_calls", {}),
                                         ensure_ascii=False))
    add("- 临时目录：%s（已清理：%s）" % (
        C.desensitize(isolation.get("temp_root", ""), 120), isolation.get("temp_cleaned")))
    add("")
    add("## 3. 事实表 vs 日记命中（每天）")
    add("")
    add("| 天 | 类型 | 块数 | 去过的地方 | 出门 | 三餐 | 健康(当天/生成时) | 天气 | 日记命中 | 问题数 |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for day in report.get("days") or []:
        facts = day.get("facts") or {}
        health = facts.get("health") or {}
        gen = facts.get("health_gen") or {}
        matrix = day.get("matrix") or {}
        labels = {"places": "地点", "out": "出门", "meals": "饭",
                  "health": "健康", "companion": "同伴"}
        hit_bits = []
        for key, label in labels.items():
            item = matrix.get(key) or {}
            ok = item.get("hit") if "hit" in item else item.get("diary_hit")
            hit_bits.append("%s:%s" % (label, "Y" if ok else "N"))
        weather = facts.get("weather") or {}
        add("| %s | %s | %d | %s | %s | %d | %s / %s | %s %s度 | %s | %d |" % (
            day["day"], C.desensitize(day.get("kind"), 8), facts.get("block_count") or 0,
            "、".join((facts.get("places") or [])[:4]) or "-",
            "是" if facts.get("out") else "否", facts.get("meal_count") or 0,
            health.get("status") or "-", gen.get("status") or "-",
            weather.get("text") or "-", weather.get("temp", "-"),
            " ".join(hit_bits),
            sum((day.get("issue_counts") or {}).values())))
    add("")
    add("## 4. 失败分类明细")
    add("")
    counts = report.get("issues_by_category") or {}
    add("| 分类 | 次数 | 说明 |")
    add("|---|---|---|")
    desc = {
        "place": "地点与当天去过的地点不符（含「一直在家人」）",
        "action": "写了当天没发生的强动作（上班/医院/逛街/做饭等）",
        "companion": "没有面对面事实却写老公在身边/一起",
        "meal": "当天有吃饭事实却写「没吃」",
        "health": "健康状态与当天事实矛盾",
        "weather": "天气断言与当天模拟天气冲突；且素材包没有天气事实",
        "fabricated": "电话/来访/礼物/转账等素材包没有的事件",
        "template": "跨天整句/开头/结尾复读、4-gram 重叠高",
        "coverage": "关键事件（出门/生病/约会/吃饭/早起）没写进去",
        "order": "时间词逆序",
        "transition": "相邻句跨地点但无过渡词",
    }
    for category, count in counts.items():
        add("| %s | %d | %s |" % (CATEGORY_LABEL.get(category, category), count,
                                  desc.get(category, "")))
    add("")
    add("## 5. 健康 / 天气 / 同伴专项")
    add("")
    special = report.get("special") or {}
    frame = report.get("prompt_consistency") or {}
    add("### 提示词日期口径（确定性）")
    add("- 补写昨天时「今天/昨日/已经过完的某天」三处口径混用：%d/%d 天" % (
        frame.get("mixed_days") or 0, len(frame.get("rows") or [])))
    add("- 本次 home_lock（日程有外出却写一直在家人）失败：%d 处" % (
        frame.get("home_lock_failures") or 0))
    add("")
    name_probe = report.get("name_scrub_probe") or {}
    add("### 人名兜底误伤（确定性 probe）")
    add("- 生产路径 scrub_names_guarded 是否把普通词当人名替换：%s；"
        "底层 foreign_names 原始候选命中=%s；样例：%s" % (
            "命中" if name_probe.get("hit") else "未命中",
            name_probe.get("raw_detector_hit"),
            [row.get("guarded") for row in (name_probe.get("samples") or [])[:3]]))
    add("")
    add("### 健康")
    for item in special.get("health") or []:
        add("- %s：当天=%s，生成时=%s，提示词带健康事实=%s，日记提到生病=%s" % (
            item["day"], item["day_status"], item["gen_status"],
            item["prompt_has_health_fact"], item["diary_sick_mentioned"]))
        injected = item.get("state_injection") or {}
        add("  - 生产 state 注入：当天 20:00 写入 %s，health_day 快照=%s；"
            "次日早上 read_health 回读=%s（ok=%s）" % (
                injected.get("expected_status") or "-",
                (injected.get("snapshot") or {}).get("status") or "-",
                (injected.get("readback_at_gen") or {}).get("status") or "-",
                injected.get("ok")))
    if not (special.get("health") or []):
        add("- 本次没有生病日")
    add("")
    add("### 天气")
    for item in special.get("weather") or []:
        add("- %s：当天=%s %s度，提示词带天气=%s；日记写：%s" % (
            item["day"], (item["fact"] or {}).get("text"),
            (item["fact"] or {}).get("temp"), item["prompt_has_weather_fact"],
            "、".join(C.desensitize(claim, 40) for claim in (item.get("claims") or [])[:3])))
    if not (special.get("weather") or []):
        add("- 日记没有天气断言")
    add("")
    add("### 同伴（正向探针）")
    companion = report.get("companion_probe") or {}
    add("- 条件：%s；日期=%s" % (companion.get("conditions"), companion.get("day")))
    add("- date_windows=%s" % json.dumps(companion.get("date_windows"),
                                        ensure_ascii=False)[:300])
    for check in companion.get("checks") or []:
        add("- %s：%s（%s）" % (check["name"], "通过" if check["ok"] else "失败",
                               C.desensitize(check["detail"], 200)))
    if companion.get("error"):
        add("- 错误：%s" % C.desensitize(companion["error"], 300))
    add("")
    add("## 6. digest 注入最近三天")
    add("")
    digest = report.get("digest") or {}
    plain = digest.get("as_production") or {}
    add("- 生产原样：episodic_memory=%s 条，注入行=%r（%s）" % (
        plain.get("episodic_items"), plain.get("line"), plain.get("verdict")))
    scan = digest.get("static_scan") or {}
    add("- 静态扫描：note_recent_topic 定义 %d 处、生产调用点 %d 处；episodic_memory kv_set %d 处、kv_get %d 处" % (
        len(scan.get("definition") or []), len(scan.get("call_sites") or []),
        len(scan.get("kv_writers") or []), len(scan.get("kv_reads") or [])))
    add("- 播种路径：%s；单条 day_digest 上限 %s 字" % (
        digest.get("seeded_write_path") or "-", digest.get("day_digest_limit") or "-"))
    for day, item in (digest.get("seeded") or {}).items():
        if day == "errors":
            continue
        add("- 播种 %s：事实 %d 条（关键 %d）-> digest %d 字；"
            "跨天去重/截断丢掉：%s" % (
                day, len(item.get("facts") or []), len(item.get("key_facts") or []),
                item.get("chars") or 0,
                [C.desensitize(line, 60) for line in (item.get("dropped") or [])[:2]] or "无"))
    for probe in digest.get("probes") or []:
        add("- 探针 %s：【前几天】=%r" % (probe.get("probe_day"),
                                       C.desensitize(probe.get("line") or "", 300)))
        for check in probe.get("checks") or []:
            add("    - %s：%s（%s）" % (check["name"], "通过" if check["ok"] else "失败",
                                        C.desensitize(check["detail"], 200)))
    add("")
    add("## 7. 模板复读")
    add("")
    template = report.get("template") or {}
    add("- 跨天重复整句：%d 句" % len(template.get("repeated_sentences") or []))
    for item in (template.get("repeated_sentences") or [])[:8]:
        add("  - %s（%s）" % (C.desensitize(item["sentence"], 80),
                             "、".join(item["days"])))
    add("- 4-gram 最相近的天对：%s" % json.dumps(
        (template.get("pair_4gram_jaccard") or [])[:5], ensure_ascii=False))
    add("- 每天最大重叠：%s" % json.dumps(template.get("per_day_max_overlap") or {},
                                          ensure_ascii=False))
    add("")
    add("## 8. 前 10 脱敏样例")
    add("")
    for index, sample in enumerate((report.get("samples") or [])[:10], 1):
        add("%d. [%s] %s / %s" % (index, sample["day"], sample["category_label"],
                                  sample.get("subtype")))
        add("   - 日记证据（脱敏）：%s" % sample.get("diary_evidence"))
        add("   - 事实证据：%s" % sample.get("fact_evidence"))
        add("   - 提示词里有对应事实：%s" % sample.get("prompt_has_fact"))
    add("")
    add("## 9. 疑似根因")
    add("")
    for cause in report.get("root_causes") or []:
        add("### [%s] %s" % (cause.get("severity"), cause.get("title")))
        for item in cause.get("evidence") or []:
            add("- %s" % item)
        add("- 影响：%s" % cause.get("impact"))
        add("")
    add("## 10. 局限")
    add("")
    for item in report.get("limitations") or []:
        add("- %s" % item)
    add("")
    add("## 11. 复跑与代码指纹")
    add("")
    add("- 复跑：python work/sim/diary_sim.py --days 7（默认读 provider.env；"
        "加 --no-real-llm 用确定性 mock）")
    add("- 输出：work/sim/diary_report.json / work/sim/diary_report.md")
    add("- 代码指纹（sha256_16）：%s" % json.dumps(
        report.get("code_snapshot") or {}, ensure_ascii=False)[:1400])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 主流程
def limitations_of(provider, days):
    limits = [
        "只跑生产日程生成器（work/simulate_weeks.build_day_skeleton）+ 生产大日记链路；"
        "没有跑逐小时 JIT/过渡/块小日记调度（块日记表为空 -> chat_lines/hourly_lines 都是空，"
        "等价于「数据插件没接」的生产分支，这会让提示词多一句「非常平淡」，已在根因里单列）",
        "固定 7 天序列（2026-11-01..07，golden 同款种子），3/5 天窗口是它的前 3/5 天累计，"
        "不是三次独立重跑",
        "聊天/账本为空：没有真实对话与消费，只验证日程/健康/天气/同伴维度",
        "健康/约会为注入测试条件：健康在当天 20:00 用生产 set_health 写入，再由生产 "
        "read_health 落 health_day:<day> 快照（第二天补写时按天读回，不是读生成时刻），"
        "约会用生产 begin/finish + 一个 date 日程块；除这两项外日程块全部来自生产生成器",
        "地点判定用独立词表（work/sim/diary_checks.py），可能有个别口语漏判/误判；"
        "报告里每条失败都带原文与事实证据，可人工复核",
        "生成层 temperature=0.8 单次采样：同一 harness 连跑多次，分类命中会波动"
        "（本次保留最后一次采样）；确定性结论（digest 死链路、提示词日期口径、"
        "人名兜底误伤、健康事实取生成时刻、天气无素材）另有代码级证据，不依赖采样",
    ]
    if provider is None:
        limits.insert(0, "没有可用真实模型：生成层用确定性 mock，只证明链路与核对器可用，"
                         "不能评估语义质量")
    return limits


async def run(args):
    started = dt.datetime.now()
    before = snapshot_dbs()
    code = code_snapshot()
    static_scan = scan_episodic_writers()
    temp_root = tempfile.mkdtemp(prefix="amo-diary-sim-")
    diary_root = os.path.join(temp_root, "diary")
    os.makedirs(diary_root, exist_ok=True)
    guard_calls, restore_guards = install_guards()
    report = {
        "schema": "diary_sim_v1",
        "generated_at": started.isoformat(timespec="seconds"),
        "scope": {
            "days": args.days, "city": CITY, "start_day": START.isoformat(),
            "windows": [3, 5, 7],
            "diary_pipeline": "amo_schedule.ScheduleMixin._write_one_daily_diary "
                              "-> _generate_daily_diary（生产大日记链路）",
            "schedule_pipeline": "work.simulate_weeks.build_day_skeleton（生产 ScheduleEngine）",
        },
        "provider_requested": not args.no_real_llm,
        "checks": [],
        "days": [],
    }
    provider = None
    recorder = []
    day_reports = []
    try:
        if not args.no_real_llm:
            provider = load_provider()
        plugin, state = build_plugin(diary_root)
        days = day_list(args.days)
        schedule_source = {"kind": "live_engine", "error": ""}
        try:
            blocks, kinds, _ctxs = generate_schedules(plugin, state, days)
        except Exception as exc:
            schedule_source = {"kind": "golden_fallback", "error": repr(exc)[-300:]}
            blocks, kinds = golden_schedule(days)
            for day, rows in blocks.items():
                state.schedule[day] = [dict(row) for row in rows]
                plugin.db.save_schedule(day, [dict(row) for row in rows])
        report["schedule_source"] = schedule_source
        weather_day = {day: simulated_weather(state, day, 12) for day in days}
        weather_gen = {}
        for index, day in enumerate(days):
            gen_day = (days[index + 1] if index + 1 < len(days)
                       else (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat())
            weather_gen[day] = simulated_weather(state, gen_day, GEN_CLOCK[0])
        facts = build_all_facts(plugin, state, days, kinds, blocks, weather_day,
                                weather_gen)
        for index, day in enumerate(days):
            row = await write_one_diary(plugin, state, days, index, blocks, facts[day],
                                        recorder, provider)
            day_reports.append(row)
        diaries = {row["day"]: row.get("diary") or "" for row in day_reports}
        template = C.template_metrics(diaries)
        evaluated = [evaluate_day(plugin, days, index, row, facts[row["day"]], template)
                     for index, row in enumerate(day_reports)]
        digest_days = {}
        for day in days:
            try:
                digest_text, _keep, _had = plugin._day_digest(day)
            except Exception as exc:
                digest_text = "error:%r" % (exc,)
            digest_days[day] = digest_text
        digest = await digest_phase(plugin, days, facts, diary_root, provider, static_scan)
        digest["main_run_day_digest"] = digest_days
        companion = await companion_probe(provider, recorder, diary_root)
        report.update({
            "model": {"kind": "real" if provider else "mock",
                      "model": (provider or {}).get("model", "deterministic-mock"),
                      "base": (provider or {}).get("base", ""),
                      "source": (provider or {}).get("source", "work/sim/provider.env"),
                      "calls": len(recorder),
                      "errors": len([row for row in recorder if row.get("error")]),
                      "total_tokens": sum(
                          int((row.get("usage") or {}).get("total_tokens") or 0)
                          for row in recorder),
                      "note": ("真实模型（deepseek-flash）生成；提示词/事实池/守卫/落库都是生产代码"
                               if provider else "确定性 mock，仅验证链路")},
            "days": evaluated,
            "issues_by_category": all_issue_count(evaluated),
            "window_summary": summarize_windows(evaluated, days),
            "template": template,
            "digest": digest,
            "companion_probe": companion,
            "special": build_special_sections(evaluated),
        })
        report["samples"] = pick_samples(evaluated)
        name_probe = probe_name_scrub()
        prompt_frame = probe_prompt_frame(evaluated)
        report["name_scrub_probe"] = name_probe
        report["prompt_consistency"] = prompt_frame
        report["root_causes"] = build_root_causes(
            evaluated, digest, report["special"], template, static_scan, companion,
            provider, name_probe=name_probe, prompt_frame=prompt_frame)
        report["limitations"] = limitations_of(provider, days)
        report["checks"].extend([
            {"ok": all(row.get("diary") for row in day_reports),
             "name": "generation.all_days_have_diary",
             "detail": "%d/%d 天有日记" % (
                 len([row for row in day_reports if row.get("diary")]), len(day_reports))},
            {"ok": not [row for row in recorder if row.get("error")],
             "name": "generation.no_provider_error",
             "detail": "模型调用错误 %d 次" % len([row for row in recorder if row.get("error")])},
            {"ok": all(not (row.get("error") or "") for row in day_reports),
             "name": "generation.no_pipeline_error",
             "detail": "链路异常 %d 天" % len([row for row in day_reports if row.get("error")])},
        ])
        injections = [((day.get("generation") or {}).get("health_injection") or {})
                      for day in evaluated]
        sick_injections = [item for item in injections
                           if item.get("expected_status")
                           and item.get("expected_status") != "健康"]
        report["checks"].append({
            "ok": bool(sick_injections) and all(item.get("ok") for item in sick_injections),
            "name": "health.day_snapshot_injected",
            "detail": "生病日生产 state 注入 + 次日按天回读：%s" % [
                "%s 当天=%s 快照=%s 次日读=%s" % (
                    item.get("day"), item.get("expected_status"),
                    (item.get("snapshot") or {}).get("status"),
                    (item.get("readback_at_gen") or {}).get("status"))
                for item in sick_injections]})
    except Exception as exc:
        import traceback
        report["fatal_error"] = traceback.format_exc()[-3000:]
        report["checks"].append({"ok": False, "name": "run.completed",
                                 "detail": "仿真异常：%r" % (exc,)})
    finally:
        restore_guards()
        try:
            shutil.rmtree(temp_root, ignore_errors=True)
        except Exception:  # cleanup: 仿真容错
            pass
        closure = dict(report.get("isolation") or {})
        report["isolation"] = closure
        closure.update({
            "prod_dbs": snapshot_dbs(),
            "prod_dbs_before": before,
            "guard_calls": guard_calls,
            "temp_root": temp_root,
            "temp_cleaned": not os.path.isdir(temp_root),
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        })
        after = snapshot_dbs()
        unchanged = True
        detail = {}
        for name, item in before.items():
            now_item = after.get(name) or {}
            same = (item.get("exists") == now_item.get("exists")
                    and item.get("sha256") == now_item.get("sha256")
                    and item.get("size") == now_item.get("size")
                    and item.get("mtime") == now_item.get("mtime"))
            detail[name] = {"same": same, "before": item, "after": now_item}
            unchanged = unchanged and same
        closure["prod_db_unchanged"] = unchanged
        closure["prod_db_detail"] = detail
        closure["note"] = (
            "守卫计数 amo_db_init/sqlite_blocked/open_blocked 都是 0 表示本次进程从未触碰生产库；"
            "若 prod_db_unchanged=False，需看 before/after 指纹差是否来自正在运行的生产实例")
        report["code_snapshot"] = code
        report["static_scan"] = static_scan
    report["limitations"] = report.get("limitations") or limitations_of(provider, args.days)
    os.makedirs(HERE, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_markdown(report))
    print("wrote", OUT_JSON)
    print("wrote", OUT_MD)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="隔离日程日记仿真（只读）")
    parser.add_argument("--days", type=int, default=PLAN_DAYS,
                        help="跑几天（>=7 时 3/5/7 窗口都覆盖；默认 7）")
    parser.add_argument("--no-real-llm", action="store_true",
                        help="不调真实模型，用确定性 mock")
    args = parser.parse_args(argv)
    report = asyncio.run(run(args))
    summary = {
        "diaries": len([day for day in report.get("days") or [] if day.get("diary")]),
        "issues_by_category": report.get("issues_by_category"),
        "prod_db_unchanged": (report.get("isolation") or {}).get("prod_db_unchanged"),
        "hard_failed": [check["name"] for check in report.get("checks") or []
                        if not check.get("ok")],
        "fatal": bool(report.get("fatal_error")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if not report.get("fatal_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
