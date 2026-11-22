# -*- coding: utf-8 -*-
"""主动消息/未回复追问的沉默 6 小时闸（amo_presence 接线验收，2026-11-22）。

真实飞书记录：09:16 用户一直没回，她还在问「等了十五分钟」；07:30 连发 7 条催问。
这里验证：沉默超过 6 小时后，未回复追问和闲聊主动都被拦下；问候/时间点提醒仍放行。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_proactive  # noqa: E402
import clock  # noqa: E402
import group_scene  # noqa: E402
from test_cov2_proactive import (  # noqa: E402
    FakeLLM,
    FakeProactive,
    ProactiveDB,
    _send_lines,
)

NOW = clock.now_ts()


def _pending(age_seconds=25 * 60):
    return {"sent_at": NOW - age_seconds, "kind": "greet", "text": "在忙吗",
            "umo": "lark:private:u1", "followup": False, "mood": True}


class FollowupSilenceTests(unittest.TestCase):
    def _plugin(self, last_seen):
        db = ProactiveDB(profiles=[{"uid": "u1", "last_seen": last_seen,
                                    "private_umo": "lark:private:u1"}])
        p = FakeProactive(db=db)
        p.config["proactive_followup_minutes"] = 20
        p.config["proactive_no_reply_minutes"] = 0
        p._followup_text_live = _async("忙完了瞄一眼嘛")
        p.db.kv["proactive_pending_u1"] = _pending()
        return p

    def test_silent_six_hours_no_followup(self):
        p = self._plugin(last_seen=NOW - 7 * 3600)
        asyncio.run(p._check_proactive_pending())
        self.assertEqual(p.sent, [], "沉默 6 小时后不许再追问")
        pending = p.db.kv.get("proactive_pending_u1") or {}
        self.assertFalse(pending.get("followup"))

    def test_recent_user_still_gets_followup(self):
        p = self._plugin(last_seen=NOW - 60)
        asyncio.run(p._check_proactive_pending())
        self.assertTrue(p.sent, "用户刚说过话、只是没回主动消息，仍可以温柔追问")
        pending = p.db.kv.get("proactive_pending_u1") or {}
        self.assertTrue(pending.get("followup"))


class ProactiveSilenceTests(unittest.TestCase):
    def test_proactive_blocked_when_presence_says_no(self):
        from test_cov2_proactive import OneTests

        p = OneTests()._plugin(llm=FakeLLM(text="在干嘛"))
        profile = {"uid": "u1", "name": "老公", "last_seen": NOW}
        with mock.patch.object(amo_proactive._presence, "can_proactive", lambda *a, **k: False), \
                mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(p, profile, {}, mock.Mock(hour=15), False, False))
        self.assertEqual(p.llm.calls, [])
        self.assertTrue(p.db.kv.get("proactive_slot:u1:%s:15" % amo_proactive.today_str()))

    def test_morning_goodnight_reminder_not_blocked_by_presence(self):
        """早晚问候和明确时间点提醒即使在沉默期也允许（策略在 amo_presence 里判）。"""
        import amo_presence

        p = _PresenceProbe()
        profile = {"uid": "u1", "last_seen": NOW - 7 * 3600}
        for kind in ("morning", "goodnight", "schedule_reminder"):
            self.assertTrue(amo_presence.can_proactive(p, profile, kind, now=NOW), kind)


class _PresenceProbe:
    def __init__(self):
        from test_presence_gates import _plugin as make

        self._inner = make(last_seen=clock.now_ts() - 7 * 3600)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _async(value):
    async def _run(*args, **kwargs):
        return value

    return _run


if __name__ == "__main__":
    unittest.main()
