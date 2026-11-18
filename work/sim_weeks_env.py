# -*- coding: utf-8 -*-
"""sim_weeks_env：simulate_weeks 的环境层（真实库守卫 / 临时根 / 假插件 / 断言原语）。

2026-11-17 拆分：从 work/simulate_weeks.py 逐字搬出（常量 + 守卫 + FakeDB/FakePlugin +
时间轴/断言/日记/审计原语），公开名字与调用路径不变。本模块只依赖外部库与 sim_lib，
不 import work.simulate_weeks，避免循环导入。
"""
from __future__ import annotations

import asyncio
import builtins
import datetime as dt
import hashlib
import io
import json
import os
import sqlite3
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
import amo_state_common as common  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402
import work.preview_day as preview  # noqa: E402
import work.simulate_day as sim  # noqa: E402

DEFAULT_CITY = "东莞"
START_DAY = dt.date(2026, 11, 1)          # 11-01 是 1 号（账单日），11-15 是 15 号（工资日）
DATA_ROOT = os.path.abspath(os.path.join(ROOT, os.pardir, os.pardir))
REAL_DIR = os.path.join(DATA_ROOT, "plugin_data", "Astrbot_amoupup_forever")
REAL_DATA_DIR = os.path.join(DATA_ROOT, "plugin_data", "Astrbot_amoupup_data")
REAL_DBS = (os.path.join(REAL_DIR, "amo_forever.db"),
            os.path.join(REAL_DATA_DIR, "amo_data.db"))
TMP_ROOT = sim_lib.temp_root()
TMP_DIR = sim_lib.temp_dir("weeks-")

#: 疑似真实路径的判定：真实插件数据目录下、或同名真实库文件
def _is_real_path(path):
    try:
        text = str(path)
        if not text or text in (":memory:",):
            return False
        absolute = os.path.abspath(text)
    except Exception:
        return False
    if absolute == REAL_DIR or absolute.startswith(REAL_DIR + os.sep):
        return True
    return os.path.basename(absolute) in tuple(os.path.basename(p) for p in REAL_DBS)


class RealWriteGuard:
    """写入守卫：真实库只许读，任何写/建连都直接失败。"""

    def __init__(self):
        self.violations = []
        self._open = None
        self._io_open = None
        self._connect = None

    def install(self):
        guard = self
        self._open = builtins.open
        self._io_open = io.open
        self._connect = sqlite3.connect

        def guard_open(file, mode="r", *args, **kwargs):
            guard.check(file, mode)
            return guard._open(file, mode, *args, **kwargs)

        def guard_io_open(file, mode="r", *args, **kwargs):
            guard.check(file, mode)
            return guard._io_open(file, mode, *args, **kwargs)

        def guard_connect(database, *args, **kwargs):
            guard.check(database, "sqlite3.connect")
            return guard._connect(database, *args, **kwargs)

        builtins.open = guard_open
        io.open = guard_io_open
        sqlite3.connect = guard_connect

    def uninstall(self):
        if self._open is not None:
            builtins.open = self._open
            io.open = self._io_open
            sqlite3.connect = self._connect
            self._open = None

    def check(self, file, mode):
        if not _is_real_path(file):
            return
        writes = True
        if mode == "sqlite3.connect":
            writes = True
        else:
            text = str(mode or "r")
            writes = any(ch in text for ch in "wax+")
        if not writes:
            return
        message = "REAL_PATH_WRITE '%s' mode=%s" % (file, mode)
        self.violations.append(message)
        raise RuntimeError(message)


def snapshot_real_dbs():
    """真实库指纹：sha256/mtime/size；文件不存在记 exists=False。"""
    out = {}
    for path in REAL_DBS:
        name = os.path.basename(path)
        try:
            stat = os.stat(path)
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            out[name] = {"exists": True, "sha256": digest.hexdigest(),
                         "mtime": round(stat.st_mtime, 3), "size": stat.st_size}
        except FileNotFoundError:
            out[name] = {"exists": False, "sha256": "", "mtime": 0, "size": 0}
        except Exception as exc:
            out[name] = {"exists": None, "error": str(exc)}
    return out


# ---------------------------------------------------------------- 假 DB / 假插件
class WeekDB(sim.FakeDB):
    """在 simulate_day.FakeDB 上补 block_diary（按块身份的块级小日记）。"""

    def __init__(self):
        super().__init__()
        self.block_diary = {}

    # ---- 块级小日记 ----
    def save_block_diary(self, day, block_id, text, start="", end="", place="",
                         version=None, status="valid", source=""):
        try:
            from amo_diary_guard import sanitize_diary
            text = sanitize_diary(text)
        except Exception:  # cleanup
            pass
        if version is None:
            version = self.schedule_version(day)
        self.block_diary[(str(day), str(block_id))] = {
            "day": str(day), "block_id": str(block_id), "start": str(start or ""),
            "end": str(end or ""), "place": str(place or ""), "text": str(text or ""),
            "updated": time.time(), "version": int(version),
            "status": str(status or "valid"), "invalidated_at": 0,
            "source": str(source or ""),
        }

    def get_block_diary_record(self, day, block_id):
        row = self.block_diary.get((str(day), str(block_id)))
        return dict(row) if row else None

    def get_block_diary(self, day, block_id):
        row = self.block_diary.get((str(day), str(block_id)))
        return str((row or {}).get("text") or "")

    def list_block_diaries(self, day):
        rows = [dict(row) for (day_key, _bid), row in self.block_diary.items()
                if day_key == str(day)]
        return sorted(rows, key=lambda row: str(row.get("start") or ""))

    def mark_block_diaries_stale(self, day, from_start, version=None):
        try:
            start_hour = int(str(from_start)[:2])
        except (TypeError, ValueError):
            start_hour = 0
        marked = 0
        for row in self.block_diary.values():
            if row.get("day") != str(day):
                continue
            try:
                hour = int(str(row.get("start") or "0")[:2])
            except (TypeError, ValueError):
                hour = 0
            if hour < start_hour:
                continue
            if row.get("status") in ("stale", "frozen", "archived"):
                continue
            row["status"] = "stale"
            row["invalidated_at"] = time.time()
            marked += 1
        return marked

    def mark_diaries_stale(self, day, start_hour, version):
        return self.mark_block_diaries_stale(day, start_hour, version)

    def clear_block_diaries(self, day):
        for key in [key for key in self.block_diary if key[0] == str(day)]:
            self.block_diary.pop(key, None)


class WeekPlugin(sim.FakePlugin):
    def __init__(self, city=DEFAULT_CITY, day="2026-11-01"):
        super().__init__(city=city, day=day)
        self.db = WeekDB()
        self.wallet = Wallet(self.db)
        self.wallet.ensure_schema()
        self.llm = None
        self.config.update({
            "block_diary_enabled": True,
            "diary_enabled": True,
            "location_rules_enabled": True,
            "travel_enabled": True,
            "travel_days_max": 3,
            "schedule_city": city,
            "schedule_home": "家",
            "schedule_workplace": "%s·写字楼" % city,
            "schedule_car": "小电驴",
            "travel_price_multiplier": 1.6,
            "travel_return_cost": 800,
            "fact_binder_mode": "strict",
        })


class TextLLM:
    """假 LLM：返回 JSON 小日记（正常文本）。"""

    def __init__(self, text):
        self.text = str(text)
        self.calls = 0

    async def chat_raw(self, umo, system, prompt):
        self.calls += 1
        return json.dumps({"diary": self.text}, ensure_ascii=False)


NORMAL_TEXT = {
    "hospital": "医院长椅上等叫号，消毒水味一直往鼻子里钻。",
    "office": "工位上键盘敲得噼啪响，肩膀已经僵成一块了。",
    "mall": "商场空调很足，兜了两圈也没挑出想买的东西。",
    "scenic": "景区风大，头发被吹得乱七八糟，照片拍了好几张。",
    "hotel": "酒店窗帘没拉，灯亮得晃眼，行李还摊在地上。",
    "restaurant": "楼下小馆油烟味很重，我扒了两口就放下筷子。",
    "home": "窝在沙发里把腿盘起来，电视开着也没看进去。",
    "road": "车上人不多，靠着窗打了个盹，站名都没听清。",
    "sleep": "困得眼睛都睁不开，脑袋一沾枕头就沉下去了。",
}


def make_plugin(day, city=DEFAULT_CITY):
    plugin = WeekPlugin(city=city, day=day)
    state = amo_human_state.HumanState(plugin)
    plugin.state = state
    state.engine = ScheduleEngine(state)
    return plugin, state


def rebuild(plugin, day):
    """重载：共享同一个 DB（真实库），只重建内存对象。"""
    fresh = WeekPlugin(city=plugin.config.get("schedule_city", DEFAULT_CITY), day=day)
    fresh.db = plugin.db
    fresh.wallet = Wallet(fresh.db)
    fresh.wallet.ensure_schema()
    fresh.config = dict(plugin.config)
    fresh.llm = plugin.llm
    state = amo_human_state.HumanState(fresh)
    fresh.state = state
    state.engine = ScheduleEngine(state)
    return fresh, state


def rows_of(plugin, state, day):
    return [dict(row) for row in (state.schedule.get(day) or plugin.db.get_schedule(day) or [])
            if isinstance(row, dict)]


def at(day, hour, minute=0):
    return sim_lib.at(day, hour, minute)


def covering(rows, minute):
    found = []
    for row in rows:
        current = sim.span(row)
        if current and current[0] <= minute < current[1]:
            found.append(row)
    return found


def bid_of(day, row):
    return common.block_id(day, row)


def record_of(plugin, day, row):
    return plugin.db.get_block_diary_record(day, bid_of(day, row)) or {}


def day_meta(day):
    try:
        return dict(amo_shared.day_meta(day) or {})
    except Exception:
        return {}


def day_kind_of(day):
    meta = day_meta(day)
    if meta.get("rest") or meta.get("weekend") or meta.get("holiday"):
        return "rest"
    return "workday"


# ---------------------------------------------------------------- 断言工具
def fail(checks, name, detail, hard=True):
    checks.append({"name": name, "ok": False, "detail": str(detail)[:400], "hard": hard})


def ok(checks, name, detail=""):
    checks.append({"name": name, "ok": True, "detail": str(detail)[:400], "hard": False})


def diary_llm(index, item):
    """一半错地点词（被窝/茶几/工位/床），一半正常文本。"""
    wrong = (index % 2 == 0)
    if wrong:
        return sim.WrongLLM(sim.wrong_text_for(item)), True
    text = NORMAL_TEXT.get(sim.place_category(item), NORMAL_TEXT["home"])
    return TextLLM(text), False


def generate_one_diary(plugin, state, day, item, rows, index, checks):
    """跑真实块日记管线（expand_block），断言生成/地点闸/落库。"""
    llm, wrong = diary_llm(index, item)
    plugin.llm = llm
    try:
        result = asyncio.run(state.expand_block(day, dict(item), list(rows)))
    except Exception as exc:
        fail(checks, "diary.exception", "%s-%s %r" % (item.get("start"), item.get("end"), exc))
        return ""
    text = str(result.get("diary") or "")
    start = str(item.get("start") or "")
    if result.get("pending"):
        fail(checks, "diary.pending_on_ended", "%s-%s" % (start, item.get("end")))
        return ""
    if not text.strip():
        fail(checks, "diary.empty", "%s-%s %s" % (start, item.get("end"), item.get("activity")))
        return ""
    violations = loc.check(text, item.get("place"), item.get("zone"))
    if violations:
        fail(checks, "diary.place_gate", "%s-%s %s %s" % (
            start, item.get("end"), "、".join(violations), text[:60]))
    if wrong and text == str(getattr(llm, "text", "")) and not result.get("fallback"):
        fail(checks, "diary.wrong_not_rewritten", "%s-%s %s" % (start, item.get("end"), text[:60]))
    record = record_of(plugin, day, item)
    if not record:
        fail(checks, "diary.not_persisted", "%s-%s" % (start, item.get("end")))
    elif str(record.get("status") or "") not in ("valid", "frozen"):
        fail(checks, "diary.status", "%s-%s status=%s" % (start, item.get("end"), record.get("status")))
    return text


def check_pending(plugin, state, day, rows, minute, checks):
    """未结束块：点开必须 pending，且不能返回旧文本（即使缓存里有）。"""
    target = None
    for row in rows:
        span = sim.span(row)
        if not span:
            continue
        if span[0] <= minute < span[1]:
            target = row
            break
    if target is None:
        fail(checks, "pending.no_block", "minute=%d" % minute)
        return
    bid = bid_of(day, target)
    legacy = "我在医院走廊排了很久的队，消毒水味很重。（迁移旧文）"
    prior = plugin.db.get_block_diary_record(day, bid)
    plugin.db.save_block_diary(day, bid, legacy, start=str(target.get("start") or ""),
                               end=str(target.get("end") or ""),
                               place=str(target.get("place") or ""), status="valid")
    plugin.llm = TextLLM("不该被调用的小日记")
    try:
        result = asyncio.run(state.expand_block(day, dict(target), list(rows)))
    except Exception as exc:
        fail(checks, "pending.exception", repr(exc))
        result = {}
    try:
        if not result.get("pending"):
            fail(checks, "pending.flag", "block %s-%s 未结束却返回了 %r" % (
                target.get("start"), target.get("end"), str(result.get("diary") or "")[:40]))
        if str(result.get("diary") or "").strip():
            fail(checks, "pending.leaked_text", "%s-%s %s" % (
                target.get("start"), target.get("end"), str(result.get("diary"))[:40]))
        if str(result.get("diary") or "") == legacy:
            fail(checks, "pending.legacy_reused", "%s-%s" % (target.get("start"), target.get("end")))
    finally:
        # 还原测试注入的旧正文，别污染当天审计
        if prior is None:
            plugin.db.block_diary.pop((str(day), str(bid)), None)
        else:
            plugin.db.block_diary[(str(day), str(bid))] = dict(prior)


def audit_day(plugin, day, now, rows, extra_warn=None):
    """同 preview_day 口径的按天审计（HARD/WARN）。"""
    enriched = []
    for row in rows:
        item = dict(row)
        bid = bid_of(day, item)
        record = plugin.db.get_block_diary_record(day, bid) or {}
        item["_bid"] = bid
        item["_diary"] = str(record.get("text") or "")
        item["_status"] = str(record.get("status") or "")
        enriched.append(item)
    hard, warn = preview.review(enriched, day, now)
    hard = [tuple(x) for x in hard]
    warn = [tuple(x) for x in warn]
    for entry in extra_warn or []:
        warn.append(entry)
    return hard, warn, enriched


def label_of(entry):
    row = entry[1] if len(entry) > 1 and isinstance(entry[1], dict) else {}
    extra = entry[2] if len(entry) > 2 else ""
    if isinstance(extra, (list, tuple)):
        extra = "、".join(str(x) for x in extra)
    return "%s %s-%s %s%s" % (entry[0], row.get("start"), row.get("end"),
                              str(row.get("activity") or "")[:12],
                              ("(%s)" % extra) if extra else "")
