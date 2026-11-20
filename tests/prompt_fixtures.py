# -*- coding: utf-8 -*-
"""Phase 4 提示词黄金快照的确定性桩。

不联网、不读真实 plugin_data、时钟固定在 2026-11-16 15:00，
同一次输入必须得到逐字节相同的 system prompt，供重构前后 A/B 对比。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_llm  # noqa: E402
from amo_llm import LlmMixin  # noqa: E402

FIXED_NOW = datetime(2026, 11, 16, 15, 0, 0)
DATE_BASE = 1763300000.0


def freeze_clock():
    """把 amo_llm 里的“现在”钉死，避免 hour 变化导致黄金快照漂移。"""
    amo_llm._now = lambda: FIXED_NOW


class FakeDB:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.writes = []

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.writes.append((key, value))
        self.data[key] = value


class FakeWallet:
    def balance(self):
        return 10000


class FakeWardrobe:
    def current(self):
        return {"text": "米色针织衫配牛仔裤"}


class FakeState:
    period_start = "2026-01-01"

    def prompt_text(self, snap, with_activity=False):
        return "【状态】在家，刚吃过饭。"

    def tone_text(self, snap, relation):
        return "语气温和"

    def worldview_text(self):
        return "普通小城生活。"

    def mood_words(self):
        return ""

    def set_period_start(self, value):
        self.period_start = value


class FakeEvent:
    unified_msg_origin = "aiocqhttp:PrivateMessage:10001"
    heartflow_target = ""
    heartflow_angle = ""
    heartflow_mode_hint = ""
    heartflow_triggered = False
    is_at_or_wake_command = False
    amo_impersonation = False

    def __init__(self, **over):
        for key, value in over.items():
            setattr(self, key, value)

    def get_sender_name(self):
        return "阿明"


class FakePlugin(LlmMixin):
    """只实现 _reply_system 真正会碰到的钩子；组装方法复用 LlmMixin 本体。"""

    def __init__(self, kv=None):
        self.db = FakeDB(kv)
        self.config = {}
        self.plugin_dir = ROOT
        self.state = FakeState()
        self.wallet = FakeWallet()
        self.wardrobe = FakeWardrobe()
        self.draw = None
        self.photo = None
        self.heartflow = None
        self.segment_engine = None
        self._persona_name_cache = {}
        self.admin_uids = set()
        self._config_mtime = 0.0

    def cfg(self, key, default=None):
        return default

    def cfg_str(self, key, default=""):
        return str(default)

    def cfg_bool(self, key, default=False):
        return bool(default)

    def cfg_int(self, key, default=0):
        return int(default)

    def cfg_float(self, key, default=0.0):
        return float(default)

    def get_hunger(self):
        return 80

    def _group_roster_text(self, umo, current_uid="", limit=8):
        return ""

    def _group_reply_guide(self, relation, name):
        return LlmMixin._group_reply_guide(self, relation, name)

    def _dialect_active(self):
        return False

    def _dialect_style(self):
        return ""

    async def _dialect_kb_context(self, text):
        return ""

    def _recent_bot_initiated(self, umo, seconds=3600):
        return False

    def _disturb_active(self, umo):
        return False

    def _depressed_active(self, uid):
        return False

    def _relation_network_text(self, uid):
        return ""

    async def _scenic_kb_context(self, text):
        return ""

    def _spawn(self, coro):
        coro.close()


def reply_ctx(group=False):
    scene = "work_group" if group else "private"
    umo = "aiocqhttp:GroupMessage:8888" if group else "aiocqhttp:PrivateMessage:10001"
    return {
        "umo": umo, "uid": "10001", "scene": scene, "is_owner": True,
        "sender_name": "阿明", "owner_name": "老公", "name": "阿明",
        "relation": "相恋", "persona": "你叫大肥鱼，是他的恋人。",
        "world": "普通小城生活。", "asked_activity": False,
        "state_text": "在家，刚吃过饭。", "tone": "语气温和",
        "style": "【风格】口语化", "consult": False, "recent": [],
        "transcript": "", "self_name": "大肥鱼", "self_uid": "9999",
        "self_qq_nick": "小鱼", "self_display": "大肥鱼",
        "sender_nick": "阿明", "plat": "QQ", "cash_cents": 10000,
        "silence_days": 1, "last_refusal": {}, "coax": False,
    }


def base_snap():
    return {
        "mood": {"暴躁": 0, "生气": 0, "疲惫": 0, "开心": 50},
        "health": {"status": "健康"},
        "activity": {"activity": "在家休息", "category": "rest", "place": "家"},
    }


def _parts_private():
    plugin = FakePlugin()
    return plugin, LlmMixin._reply_base_parts(
        plugin, reply_ctx(False), base_snap(), "今天中午吃什么呀？", False, "")


def system_private(note=""):
    freeze_clock()
    plugin = FakePlugin()
    ctx = reply_ctx(False)
    return asyncio.run(LlmMixin._reply_system(
        plugin, ctx, FakeEvent(), "今天中午吃什么呀？", base_snap(),
        {"relation": "相恋", "name": "阿明"}, False, "", note))


def system_group(target="", angle=""):
    freeze_clock()
    plugin = FakePlugin()
    ctx = reply_ctx(True)
    event = FakeEvent(heartflow_target=target, heartflow_angle=angle)
    return asyncio.run(LlmMixin._reply_system(
        plugin, ctx, event, "今天中午吃什么呀？", base_snap(),
        {"relation": "相恋", "name": "阿明"}, True, "", ""))


def private_parts():
    freeze_clock()
    _, parts = _parts_private()
    return parts


def group_parts(target="", angle=""):
    freeze_clock()
    plugin = FakePlugin()
    ctx = reply_ctx(True)
    event = FakeEvent(heartflow_target=target, heartflow_angle=angle)
    parts = LlmMixin._reply_base_parts(plugin, ctx, base_snap(), "今天中午吃什么呀？", True, "")
    parts.extend(LlmMixin._reply_group_parts(plugin, ctx, event, True))
    return parts


def date_blocks():
    import amo_date_context as dc

    base = DATE_BASE
    now_dt = datetime.fromtimestamp(base)
    meet_plugin = FakePlugin({
        "date_now": {"line": "正在和老公吃火锅", "until": base + 3600, "since": base - 60},
        "date_meet": {"title": "吃火锅", "place": "火锅店", "until": base + 3600},
    })
    active_plugin = FakePlugin({
        "date_now": {"line": "正在和老公逛街", "until": base + 3600, "since": base - 3600},
    })
    pause_plugin = FakePlugin({
        "date_now": {"line": "正在和老公吃火锅", "until": base + 3600, "since": base - 3600},
        "date_pause": {"at": base - 60, "until": base + 300},
    })
    ending_plugin = FakePlugin({
        "date_ending": {"fact": "约会快结束了", "until": base + 300},
    })
    home_plugin = FakePlugin({
        "date_home": {"title": "火锅", "at": base, "until": base + 3600},
    })
    saved_active, saved_paused = dc._active, dc._ds.paused
    try:
        dc._active = lambda plugin, now=None: None
        dc._ds.paused = lambda plugin, now=None: {"at": base - 60, "until": base + 300}
        pause_branch = dc.prompt_block(FakePlugin(), now_dt)
    finally:
        dc._active, dc._ds.paused = saved_active, saved_paused
    return {
        "active_meet": dc.prompt_block(meet_plugin, now_dt),
        "active": dc.prompt_block(active_plugin, now_dt),
        "pause": dc.prompt_block(pause_plugin, now_dt),
        "pause_branch": pause_branch,
        "ending": dc.prompt_block(ending_plugin, now_dt),
        "home": dc.prompt_block(home_plugin, now_dt),
        "none": dc.prompt_block(FakePlugin(), now_dt),
    }


def short_blocks():
    import short_reply

    snap = base_snap()
    return {
        "rules": short_reply.RULES,
        "prompt": short_reply.prompt_block(snap, "今天中午吃什么呀？"),
        "state_sick": short_reply.state_binding({"health": {"status": "感冒"}, "mood": {}}),
        "state_tired": short_reply.state_binding({"health": {"status": "健康"}, "mood": {"疲惫": 80}}),
        "state_angry": short_reply.state_binding({"health": {"status": "健康"}, "mood": {"生气": 70}}),
        "state_happy": short_reply.state_binding({"health": {"status": "健康"}, "mood": {"开心": 80}}),
    }


def group_scene_blocks():
    import group_scene

    out = {}
    for scene in ("private", "work_group", "friend_group", "stranger_group"):
        out[scene] = group_scene.prompt_block(scene, "阿明", "老公", False, False)
        out[scene + "_owner"] = group_scene.prompt_block(scene, "阿明", "老公", True, False)
        out[scene + "_mention"] = group_scene.prompt_block(scene, "阿明", "老公", False, True)
    return out


def lean_parts():
    import main

    return dict(main.HumanCorePlugin.LEAN_PARTS)


def collect():
    freeze_clock()
    return {
        "version": 1,
        "constants": {
            "photo_request_rule": amo_llm.PHOTO_REQUEST_RULE,
            "action_ownership_rules": amo_llm.ACTION_OWNERSHIP_RULES,
        },
        "lean_parts": lean_parts(),
        "private_parts": private_parts(),
        "private_system": system_private(""),
        "private_system_note": system_private("刚才在忙，补一句。"),
        "group_parts": group_parts("", ""),
        "group_system": system_group("", ""),
        "group_system_direct": system_group("bot", ""),
        "group_system_interject": system_group("other", "想插一句"),
        "date_blocks": date_blocks(),
        "short_blocks": short_blocks(),
        "group_scene_blocks": group_scene_blocks(),
    }
