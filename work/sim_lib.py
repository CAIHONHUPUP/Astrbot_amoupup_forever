# -*- coding: utf-8 -*-
"""Phase 18 仿真复用库：stub 安装 / 假插件 / 临时库 / 假时钟 / 断言 helper。

work/simulate_day.py、simulate_24h.py、simulate_weeks.py 都 import 这里，
不再各自复制 FakeDB/FakePlugin/时钟。行为保持与原 simulate_day 逐字一致。
"""
from __future__ import annotations

import atexit
import datetime as dt
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def install_stub():
    """把 ROOT/tests 放进 sys.path 并安装 astrbot 桩（幂等）。"""
    for path in (ROOT, TESTS):
        if path not in sys.path:
            sys.path.insert(0, path)
    import astrbot_stub
    astrbot_stub.install()
    return astrbot_stub


install_stub()

import amo_human_state  # noqa: E402
import amo_location_rules as loc  # noqa: E402
import amo_shared  # noqa: E402
import amo_transition as tr  # noqa: E402
import amo_trip as trip  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402

DEFAULT_DAY = "2026-11-16"
_ORIG: dict[str, Any] = {}
_TEMP_ROOT = None


def temp_root():
    """仿真进程独占的临时根目录（系统 temp 下，退出时整棵删除）。"""
    global _TEMP_ROOT
    if _TEMP_ROOT is None:
        _TEMP_ROOT = tempfile.mkdtemp(prefix="amo-sim-")
        atexit.register(cleanup_temp_root)
    return _TEMP_ROOT


def temp_dir(prefix="sim-"):
    return tempfile.mkdtemp(prefix=str(prefix), dir=temp_root())


def cleanup_temp_root():
    global _TEMP_ROOT
    if _TEMP_ROOT:
        shutil.rmtree(_TEMP_ROOT, ignore_errors=True)
        _TEMP_ROOT = None


def ok(checks, name, detail=""):
    checks.append({"ok": True, "name": name, "detail": detail})


def fail(checks, name, detail=""):
    checks.append({"ok": False, "name": name, "detail": detail})


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}
        self.versions = {}
        self.diaries = {}
        self.records = {}
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(":memory:")

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return [dict(row) for row in self.schedules.get(str(day), [])]

    def save_schedule(self, day, items):
        self.schedules[str(day)] = [dict(row) for row in (items or [])]

    def schedule_version(self, day):
        return int(self.versions.get(str(day), 0))

    def bump_schedule_version(self, day):
        self.versions[str(day)] = self.schedule_version(day) + 1
        return self.versions[str(day)]

    def mark_diaries_stale(self, day, start_hour, version):
        return 0

    def get_diary(self, day, start):
        return self.diaries.get((str(day), str(start)), "")

    def save_diary(self, day, start, text, version=0, status="valid", **kwargs):
        self.diaries[(str(day), str(start))] = str(text or "")
        self.records[(str(day), str(start))] = {
            "text": str(text or ""), "version": int(version or 0), "status": str(status),
        }

    def get_diary_record(self, day, start):
        return dict(self.records.get((str(day), str(start)) or {}, {}) or {}) or None

    def todos(self, day):
        return []


class FakePlugin:
    def __init__(self, city="东莞", day=DEFAULT_DAY):
        self.config = {
            "schedule_city": city, "schedule_home": "家",
            "schedule_workplace": "%s·写字楼" % city if city else "写字楼",
            "schedule_car": "小电驴", "diary_enabled": True,
            "location_rules_enabled": True, "travel_price_multiplier": 1.6,
            "travel_return_cost": 800, "travel_enabled": True,
        }
        self.db = FakeDB()
        self.day = str(day)
        self.state = None
        self.llm = None
        self.events = None
        self.wardrobe = None
        self.photo = None
        self.micro_events = None
        self._last_umo = ""
        self.wallet = Wallet(self.db)
        self.wallet.ensure_schema()
        self.memory_calls = []
        self.todo_calls = []

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_list(self, key, default=None):
        return list(self.config.get(key, default or []))

    def today(self):
        return self.day

    def _memory_uid(self):
        return "audit-uid"

    async def _data_memory_write(self, uid, content, kind, tier, scope, umo,
                                 subject="bot", idem_key="", importance=0.0, tags=""):
        self.memory_calls.append({
            "uid": uid, "content": content, "kind": kind, "tier": tier,
            "scope": scope, "subject": subject, "idem_key": idem_key,
            "importance": importance, "tags": tags,
        })
        return True


class WrongLLM:
    """始终返回错误地点的小日记；用于验证地点闸会重写或兜底。"""

    def __init__(self, text):
        self.text = text
        self.calls = 0

    async def chat_raw(self, umo, system, prompt):
        self.calls += 1
        import json
        return json.dumps({"diary": self.text}, ensure_ascii=False)


WRONG_TEXT = {
    "hospital": "我躺在被窝里，茶几上还放着外卖，整个人昏昏沉沉的，一点都不想动。",
    "office": "我窝在床上刷手机，被子裹得紧紧的，今天真不想上班，眼睛都睁不开。",
    "mall": "我在工位上摸鱼，键盘敲得噼里啪啦，心里却想着什么时候能下班回家。",
    "scenic": "我在厨房里做饭，油烟呛得我直咳嗽，窗外的风景一点也没看到，真累。",
    "hotel": "我在工位上加班，同事一直在旁边催我改文件，酒店的房间我都没来得及看。",
    "road": "我瘫在沙发上，电视开着也没看进去，脚边还放着没吃完的零食，懒得动。",
    "home": "我在工位上忙工作，电脑屏幕亮得刺眼，同事走过来跟我对了一下午的需求。",
}


#: 2026-11-17：amo_shared 按域拆成子模块后，_now() 的 _clock_now 绑定住在
#: amo_shared_time（门面只是转发），冻结时钟要把这几个模块一起改。
CLOCK_MODULES = ("amo_shared", "amo_shared_time", "amo_shared_deps")


def set_clock(fixed):
    import importlib

    if not _ORIG:
        _ORIG["clock_now"] = {}
        for name in CLOCK_MODULES:
            module = importlib.import_module(name)
            if hasattr(module, "_clock_now"):
                _ORIG["clock_now"][name] = module._clock_now
        _ORIG["trans_now"] = tr._now
        _ORIG["trip_now_dt"] = trip._now_dt
    for name in CLOCK_MODULES:
        module = importlib.import_module(name)
        if hasattr(module, "_clock_now"):
            module._clock_now = lambda *args, **kwargs: fixed
    tr._now = lambda now=None: now if now is not None else fixed

    def _clock_now_dt(now=None):
        if now is None:
            return fixed
        return _ORIG["trip_now_dt"](now)

    trip._now_dt = _clock_now_dt


def restore_clock():
    import importlib

    if not _ORIG:
        return
    for name, original in _ORIG.pop("clock_now").items():
        importlib.import_module(name)._clock_now = original
    tr._now = _ORIG.pop("trans_now")
    trip._now_dt = _ORIG.pop("trip_now_dt")


def at(day, hour, minute=0):
    return dt.datetime.combine(dt.date.fromisoformat(str(day)), dt.time(int(hour), int(minute)))


def minute_of(text, end=False):
    body = str(text or "00:00").strip()[:5]
    if body in ("24:00", "0:00"):
        return 24 * 60
    if body == "00:00":
        return 24 * 60 if end else 0
    hour_text, _, minute_text = body.partition(":")
    return int(hour_text or 0) * 60 + int(minute_text or 0)


def span(row):
    if not isinstance(row, dict):
        return None
    begin = minute_of(row.get("start"))
    finish = minute_of(row.get("end"), end=True)
    if finish <= begin:
        finish = begin + 60
    return begin, finish


def place_category(row):
    return loc.resolve(str(row.get("place") or ""), str(row.get("zone") or ""))


def make_state(city="东莞", day=DEFAULT_DAY):
    plugin = FakePlugin(city=city, day=day)
    state = amo_human_state.HumanState(plugin)
    plugin.state = state
    state.engine = ScheduleEngine(state)
    return plugin, state


def make_plugin(day=DEFAULT_DAY):
    plugin = FakePlugin(day=day)
    state = amo_human_state.HumanState(plugin)
    plugin.state = state
    state.engine = ScheduleEngine(state)
    return plugin, state


def rebuild(plugin, day):
    """保存 kv/DB 后重建插件：共享同一个 FakeDB，只重建内存对象。"""
    fresh = FakePlugin(day=day)
    fresh.db = plugin.db
    fresh.wallet = Wallet(fresh.db)
    state = amo_human_state.HumanState(fresh)
    fresh.state = state
    state.engine = ScheduleEngine(state)
    return fresh
