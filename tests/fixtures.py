# -*- coding: utf-8 -*-
"""Phase 11 共享测试桩：FakeDB / FakePlugin / FakeState / FakeWallet / FixedClock。

设计原则（和提示词黄金快照的 prompt_fixtures 不冲突）：
- 只依赖标准库，不在导入期装 astrbot 桩；测试文件自己决定是否 install()；
- 行为是「宽进严出」的字典桩：kv 读写记 writes，日程读写做深拷贝，
  让断言能看见写了几次、写的是什么；
- FixedClock 同时 patch ``clock.now/now_ts`` 和几个历史模块级 ``_now`` 绑定，
  这样旧测试直接给 ``module._now`` 赋值仍然有效，而统一时钟调用点走 clock.now()。
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Phase 18：FakeClock / temp_dir / temp_store / LogCapture / PII 断言统一从 support 复用。
from support import *  # noqa: E402,F401,F403
from support import SUPPORT_VERSION  # noqa: E402,F401

#: FixedClock 固定到的默认时刻（与提示词黄金快照同一基准）。
DEFAULT_NOW = datetime(2026, 11, 16, 15, 0, 0)


def today_at(hour=15, minute=0, second=0):
    """把时钟钉在「当天」的 hour:minute —— 日期跟着真实今天走，只有时刻固定。

    跨 0 点才会暴露的用例（时段是否过完、0-6 点免打扰、长途出发落在未来）
    用这个当冻结时刻，日期仍和 today_str() 一致，不会引入新的日期错位。
    """
    try:
        import clock as _clock
        base = datetime.combine(_clock.today(), datetime.min.time())
    except Exception:  # cleanup: 没有插件时钟时退回系统日期
        base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return base.replace(hour=int(hour), minute=int(minute),
                        second=int(second), microsecond=0)
#: FixedClock 额外要同步 patch 的历史模块级绑定（模块名, 属性名）。
_CLOCK_BINDINGS = (
    ("amo_shared", "_now"),
    ("amo_shared", "now_ts"),
    ("amo_shared", "today_str"),
    ("amo_fact_binder", "_now"),
    ("amo_human_state", "_now"),
    ("amo_human_state", "today_str"),
    ("amo_runtime", "_now"),
    ("amo_runtime", "now_ts"),
    ("amo_runtime", "today_str"),
    # 2026-11-17：amo_runtime 按 tick/context/reply 拆成三个 mixin 模块，
    # amo_shared 按时间/文本/配置/用量拆出子模块；取时绑定跟着实现走，逐个钉住。
    ("amo_runtime_context", "_now"),
    ("amo_runtime_context", "now_ts"),
    ("amo_runtime_context", "today_str"),
    ("amo_runtime_reply", "_now"),
    ("amo_runtime_reply", "now_ts"),
    ("amo_runtime_reply", "today_str"),
    ("amo_runtime_tick", "_now"),
    ("amo_runtime_tick", "now_ts"),
    ("amo_runtime_tick", "today_str"),
    ("amo_shared_time", "_now"),
    ("amo_shared_time", "now_ts"),
    ("amo_shared_time", "today_str"),
    ("amo_shared_usage", "_now"),
    ("amo_shared_activity", "_now"),
    # 这些模块是 `from clock import now as _now`（绑的是函数对象，patch clock.now 改不到）
    ("amo_social", "_now"),
)


class FakeDB:
    """字典背书的 DB 桩：kv + 日程；writes 记录每次 kv_set。"""

    def __init__(self, kv=None):
        self.data = dict(kv or {})
        # 兼容旧测试直接摸 .kv（同一个 dict，不复制）
        self.kv = self.data
        self.writes = []
        self.schedules = {}
        self.saves = 0

    # ---- kv ----
    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.writes.append((key, value))
        self.data[key] = value

    def kv_del(self, key):
        self.data.pop(key, None)

    # ---- 日程（deep copy，避免测试互相污染） ----
    def get_schedule(self, day):
        import copy
        return copy.deepcopy(self.schedules.get(day) or [])

    def set_schedule(self, day, rows):
        import copy
        self.schedules[day] = copy.deepcopy(list(rows or []))
        self.saves += 1

    # ---- 画像（宽接口，用不到也不会炸） ----
    def list_profiles(self, keyword="", limit=100):
        return []

    def get_profile(self, uid):
        return {}


class FakeWallet:
    """钱包桩：余额固定 10000 分；debit/credit 记流水。"""

    def __init__(self, balance_cents=10000):
        self._balance = int(balance_cents)
        self.debits = []
        self.credits = []

    def balance(self, account="cash"):
        return self._balance

    def debit(self, amount, reason="", idem="", **kwargs):
        self.debits.append({"amount": amount, "reason": reason, "idem": idem})
        return True

    def credit(self, amount, reason="", idem="", **kwargs):
        self.credits.append({"amount": amount, "reason": reason, "idem": idem})
        return True


class FakeState:
    """状态桩：提供提示词快照会用到的几个纯文本方法，可被测试覆盖字段。"""

    period_start = "2026-01-01"

    def __init__(self, **over):
        self.schedule = {}
        self.db = None
        self.worldview = {}
        self.health = {"status": "健康"}
        self.mood = {}
        for key, value in over.items():
            setattr(self, key, value)

    def prompt_text(self, snap=None, with_activity=False):
        return "【状态】在家，刚吃过饭。"

    def tone_text(self, snap=None, relation=""):
        return "语气温和"

    def worldview_text(self):
        return "普通小城生活。"

    def mood_words(self):
        return ""

    def set_period_start(self, value):
        self.period_start = value


class FakePlugin:
    """插件桩：cfg_* / db / state / wallet / plugin_dir，够大多数纯逻辑测试用。"""

    def __init__(self, kv=None, config=None, **cfg):
        self.db = FakeDB(kv)
        self.config = dict(config or {})
        self.config.update(cfg)
        self.plugin_dir = ROOT
        self.state = FakeState()
        self.wallet = FakeWallet()
        self.bot_name = "大肥鱼"
        self.bot_aliases = ["大肥鱼"]
        self._config_mtime = 0.0

    def cfg(self, key, default=None):
        return self.config.get(key, default)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def get_hunger(self):
        return 80


class FixedClock:
    """固定插件时钟：patch clock.now/now_ts + 历史模块级 _now，可当上下文管理器。"""

    def __init__(self, moment=None):
        self.moment = moment or DEFAULT_NOW
        self._saved = []

    def _set(self, obj, name, value):
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def start(self):
        import importlib
        try:
            clock = importlib.import_module("clock")
        except Exception:  # pragma: no cover - clock 永远可用
            clock = None
        if clock is not None:
            self._set(clock, "now", lambda *args, **kwargs: self.moment)
            self._set(clock, "now_ts", lambda: self.moment.timestamp())
        # amo_shared 的 _now()/now_ts()/today_str() 都经由模块级 _clock_now/_clock_now_ts
        # 取时；各模块 import 时拿到的是函数对象，只 patch clock.now 改不到它们。
        # 这里把 amo_shared 里的底层绑定一并钉住，任何走 amo_shared 的取时都冻结。
        for shared_name in ("amo_shared", "amo_shared_deps", "amo_shared_time"):
            try:
                shared = importlib.import_module(shared_name)
            except Exception:  # pragma: no cover - 没装 astrbot 桩时跳过
                continue
            for name, value in (("_clock_now", lambda *a, **k: self.moment),
                                ("_clock_now_ts", lambda *a, **k: self.moment.timestamp())):
                if hasattr(shared, name):
                    self._set(shared, name, value)
        for module_name, attr in _CLOCK_BINDINGS:
            try:
                module = importlib.import_module(module_name)
            except Exception:  # pragma: no cover - 没装 astrbot 桩时跳过
                continue
            if not hasattr(module, attr):
                continue
            if attr == "now_ts":
                self._set(module, attr, lambda *args, **kwargs: self.moment.timestamp())
            elif attr == "today_str":
                self._set(module, attr, lambda *args, **kwargs: self.moment.date().isoformat())
            else:
                self._set(module, attr, lambda *args, **kwargs: self.moment)
        return self

    def stop(self):
        while self._saved:
            obj, name, value = self._saved.pop()
            setattr(obj, name, value)

    def advance(self, seconds):
        from datetime import timedelta
        self.moment = self.moment + timedelta(seconds=float(seconds))
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.stop()
        return False


def make_temp_dir(prefix="amo-p2-"):
    """在 .test-tmp（conftest 已改临时目录）下建一个可写临时目录。"""
    return tempfile.mkdtemp(prefix=prefix)


def cleanup_temp_dir(path):
    shutil.rmtree(path, ignore_errors=True)
