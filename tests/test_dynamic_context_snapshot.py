# -*- coding: utf-8 -*-
"""_dynamic_context 黄金快照：5 个场景的装配顺序与文案逐字比对。

拆分 amo_runtime._dynamic_context（mo_runtime 216 行起、634 行）之前先锁住输出：
私聊 / 群聊 / 被哄睡 / 约会中 / 住酒店。时间、日期、天气、心情这些每天都会变
的字段在这里归一化，只比对固定文案和它们的先后顺序。

    python -m unittest tests.test_dynamic_context_snapshot -q
    AMO_SNAPSHOT=update python -m unittest tests.test_dynamic_context_snapshot -q
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402
import amo_human_state  # noqa: E402
import amo_runtime  # noqa: E402
import amo_runtime_context  # noqa: E402
import amo_runtime_reply  # noqa: E402
import amo_runtime_tick  # noqa: E402
import amo_shared  # noqa: E402
import clock  # noqa: E402
from astrbot.api.message_components import At  # noqa: E402
from astrbot.api.star import Context  # noqa: E402

SNAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")
FIXED_TS = 1794754200.0  # 2026-11-15 21:30:00 +08:00
FIXED_DAY = "2026-11-15"
# 拆分后 _dynamic_context 住在 amo_runtime_context；门面只转发，patch 要打到实现所在的模块。
RUNTIME_CLOCK_MODULES = (amo_runtime, amo_runtime_context, amo_runtime_reply, amo_runtime_tick)

OFFICE = {
    "start": "09:00", "end": "24:00", "activity": "对着屏幕敲周报",
    "place": "莞城区的写字楼", "zone": "office", "category": "work",
}


class FakeMessage:
    def __init__(self, components=None, message_id="m1"):
        self.message = components or []
        self.message_id = message_id


class FakeEvent:
    def __init__(self, text, umo="aiocqhttp:PrivateMessage:1001", components=None, at_bot=False):
        self.message_str = text
        self.unified_msg_origin = umo
        self.message_obj = FakeMessage(components, message_id="m1")
        self.is_at_or_wake_command = bool(at_bot)

    def get_self_id(self):
        return "999"

    def get_sender_id(self):
        return "1001"

    def get_sender_name(self):
        return "他"

    def get_platform_name(self):
        return "aiocqhttp"

    def is_private_chat(self):
        return "PrivateMessage" in self.unified_msg_origin

    def get_group_id(self):
        return ""

    def get_extra(self, key, default=None):
        return default


class LateStub:
    """熬夜状态桩：固定「在熬夜」，避免快照依赖真实时钟。"""

    def __init__(self):
        self.soothed = []

    def is_staying_up(self):
        return True

    def prompt_line(self):
        return "凌晨还在刷手机"

    def soothe(self, reason=""):
        self.soothed.append(reason)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        os.environ["AMO_SNAPSHOT"] = os.environ.get("AMO_SNAPSHOT", "")
        self.tmp = tempfile.mkdtemp(prefix="amo-ctx-snap-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            sys.modules[name] = types.ModuleType(name)
            sys.modules[name].__path__ = []
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module

        self._orig = {module.__name__: (module._now, module.now_ts, module.today_str)
                      for module in RUNTIME_CLOCK_MODULES}
        self._orig_state = (amo_human_state._now, amo_human_state.today_str)
        # Phase 11：生产代码改走插件时钟 clock.now()/now_ts()；只冻结旧入口
        # （amo_runtime._now / amo_human_state._now）已经盖不住 runtime 的取时，
        # 23:30 后会多注入一行【熬夜】导致快照漂移。这里把新入口也钉死。
        self._orig_clock = (clock.now, clock.now_ts)
        clock.now = lambda: __import__("datetime").datetime(2026, 11, 15, 21, 30)
        clock.now_ts = lambda: FIXED_TS
        for module in RUNTIME_CLOCK_MODULES:
            module._now = lambda: __import__("datetime").datetime(2026, 11, 15, 21, 30)
            module.now_ts = lambda: FIXED_TS
            module.today_str = lambda: FIXED_DAY
        amo_human_state._now = lambda: __import__("datetime").datetime(2026, 11, 15, 21, 30)
        amo_human_state.today_str = lambda: FIXED_DAY

        self.plugin = self._build()
        asyncio.run(self.plugin._ensure_ready())
        self.plugin.state.schedule = {}
        self.plugin.state.current_activity = lambda: dict(OFFICE)
        self.plugin.wardrobe.current = lambda *a, **k: {
            "top": "米色雪纺衬衫", "bottom": "黑色包臀裙", "shoes": "淡蓝色运动鞋",
        }

    def _build(self):
        plugin = main.HumanCorePlugin(Context(), {})

        async def noop():
            return None

        def spawn(coro):
            coro.close()
            return None

        plugin._spawn = spawn
        plugin._sync_with_data_plugin = noop
        plugin._warm_up = noop
        plugin._background_loop = noop
        return plugin

    def tearDown(self):
        for module in RUNTIME_CLOCK_MODULES:
            module._now, module.now_ts, module.today_str = self._orig[module.__name__]
        amo_human_state._now, amo_human_state.today_str = self._orig_state
        clock.now, clock.now_ts = self._orig_clock
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    # ---------- 归一化 ----------
    def _normalize(self, text):
        snap = self.plugin.state.snapshot()
        text = str(text or "")
        replacements = [
            (snap.get("datetime_text"), "<NOW>"),
            (snap.get("weather_text"), "<WEATHER>"),
            ((snap.get("health") or {}).get("status"), "<HEALTH>"),
        ]
        period = snap.get("period") or {}
        if period.get("phase"):
            replacements.append((str(period["phase"]) + "第", "<PERIOD>第"))
        try:
            replacements.append((self.plugin.state.mood_words(), "<MOOD>"))
        except Exception:
            pass
        for value, token in replacements:
            if value and len(str(value)) >= 3:
                text = text.replace(str(value), token)
        text = re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", text)
        text = re.sub(r"\d{1,2}:\d{2}", "<TIME>", text)
        text = re.sub(r"-?\d+(?:\.\d+)?\s*℃", "<T>", text)
        text = re.sub(r"[ \t]+$", "", text, flags=re.M)
        return text

    def _render(self, event):
        return self._normalize(asyncio.run(self.plugin._dynamic_context(event)))

    def _check(self, name, text):
        os.makedirs(SNAP_DIR, exist_ok=True)
        path = os.path.join(SNAP_DIR, name + ".txt")
        if os.environ.get("AMO_SNAPSHOT") == "update" or not os.path.exists(path):
            with open(path, "w", encoding="utf-8", newline="\n") as fp:
                fp.write(text)
            self.skipTest("snapshot written: " + name)
        with open(path, encoding="utf-8") as fp:
            expected = fp.read()
        text = "\n".join(line for line in text.splitlines() if not line.startswith("【穿搭事实】"))
        expected = "\n".join(line for line in expected.splitlines() if not line.startswith("【穿搭事实】"))
        if text != expected:
            import difflib

            diff = "\n".join(
                difflib.unified_diff(
                    expected.splitlines(), text.splitlines(),
                    fromfile="snapshot/" + name, tofile="now", lineterm="",
                )
            )
            self.fail("上下文快照变了：\n" + diff)

    # ---------- 5 个场景 ----------
    def test_private_chat(self):
        self._check("private", self._render(FakeEvent("今天好累")))

    def test_private_chat_full_mode(self):
        self.plugin.config["context_style"] = "full"
        self._check("private_full", self._render(FakeEvent("今天好累")))

    def test_group_chat(self):
        event = FakeEvent(
            "今天干嘛呢", umo="aiocqhttp:GroupMessage:1055679275",
            components=[At(qq="999")], at_bot=True,
        )
        self._check("group", self._render(event))

    def test_soothed_to_sleep(self):
        self.plugin.state.late = LateStub()
        self._check("soothe", self._render(FakeEvent("快去睡吧，别熬夜了")))

    def test_dating(self):
        self.plugin.db.kv_set("date_mood_line", {
            "line": "和老公在火锅店，他给你涮了毛肚", "until": FIXED_TS + 3600,
        })
        self._check("dating", self._render(FakeEvent("老婆，火锅好吃吗")))

    def test_at_hotel(self):
        self.plugin.state.current_activity = lambda: {
            "start": "20:00", "end": "24:00", "activity": "在酒店休息",
            "place": "酒店房间", "zone": "hotel", "category": "rest",
        }
        self.plugin.wardrobe.current = lambda *a, **k: {
            "top": "酒店浴袍", "bottom": "酒店睡裤", "shoes": "一次性棉拖鞋",
            "sleep": True, "hotel": True,
        }
        self._check("hotel", self._render(FakeEvent("在酒店干嘛呢")))


if __name__ == "__main__":
    unittest.main()
